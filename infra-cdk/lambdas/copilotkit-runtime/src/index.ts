import type {
  APIGatewayProxyEvent,
  APIGatewayProxyEventV2,
  Context,
} from "aws-lambda"
import { HttpAgent } from "@ag-ui/client"
import {
  copilotRuntimeNodeHttpEndpoint,
  CopilotRuntime,
  ExperimentalEmptyAdapter,
} from "@copilotkit/runtime"

function requireEnv(name: string): string {
  const value = process.env[name]
  if (!value) {
    throw new Error(`${name} environment variable is required`)
  }
  return value
}

const agentCoreAgUiUrl = requireEnv("AGENTCORE_AG_UI_URL")
const endpointPath = process.env.COPILOTKIT_ENDPOINT_PATH ?? "/copilotkit"
const agentName = process.env.COPILOTKIT_AGENT_NAME ?? "langgraph-ag-ui-agent"
const allowedCorsOrigins = (process.env.CORS_ALLOWED_ORIGINS ?? "")
  .split(",")
  .map((origin) => origin.trim())
  .filter((origin) => origin.length > 0)

type StreamingResponseMetadata = {
  statusCode: number
  headers?: Record<string, string>
}

type StreamingWritable = {
  write: (chunk: Buffer | string) => void
  end: () => void
}

function getHeaderValue(headers: Record<string, string | undefined>, target: string): string | undefined {
  const targetLower = target.toLowerCase()
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === targetLower && value) {
      return value
    }
  }
  return undefined
}

function toHeaderRecord(
  headers: APIGatewayProxyEvent["headers"] | APIGatewayProxyEventV2["headers"]
): Record<string, string> {
  const normalized: Record<string, string> = {}
  for (const [key, value] of Object.entries(headers ?? {})) {
    if (typeof value === "string") {
      normalized[key] = value
    }
  }
  return normalized
}

function getAllowedOrigin(requestOrigin: string | undefined): string {
  if (allowedCorsOrigins.length === 0) {
    return "*"
  }

  if (requestOrigin && allowedCorsOrigins.includes(requestOrigin)) {
    return requestOrigin
  }

  return allowedCorsOrigins[0]
}

function buildCorsHeaders(requestOrigin: string | undefined): Record<string, string> {
  const allowOrigin = getAllowedOrigin(requestOrigin)
  return {
    "access-control-allow-origin": allowOrigin,
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "Content-Type,Authorization",
    vary: "Origin",
  }
}

function getRequestUrl(event: APIGatewayProxyEvent | APIGatewayProxyEventV2): string {
  const headers = toHeaderRecord(event.headers)
  const protocol = getHeaderValue(headers, "x-forwarded-proto") ?? "https"

  if (isApiGatewayV2(event)) {
    const host = event.requestContext.domainName ?? getHeaderValue(headers, "host")
    if (!host) {
      throw new Error("Request host is missing")
    }
    const query = event.rawQueryString ? `?${event.rawQueryString}` : ""
    return `${protocol}://${host}${event.rawPath}${query}`
  }

  const host = event.requestContext.domainName ?? getHeaderValue(headers, "host")
  if (!host) {
    throw new Error("Request host is missing")
  }
  const queryPairs = Object.entries(event.queryStringParameters ?? {}).filter(
    (_entry): _entry is [string, string] => typeof _entry[1] === "string"
  )
  const queryString = new URLSearchParams(queryPairs).toString()
  const query = queryString ? `?${queryString}` : ""
  return `${protocol}://${host}${event.path}${query}`
}

function getRequestMethod(event: APIGatewayProxyEvent | APIGatewayProxyEventV2): string {
  return isApiGatewayV2(event) ? event.requestContext.http.method : event.httpMethod
}

function decodeJwtSub(authorizationHeader?: string): string | undefined {
  if (!authorizationHeader) {
    return undefined
  }
  const [scheme, token] = authorizationHeader.split(/\s+/)
  if (!scheme || scheme.toLowerCase() !== "bearer" || !token) {
    return undefined
  }

  const tokenParts = token.split(".")
  if (tokenParts.length < 2) {
    return undefined
  }

  try {
    const payloadPart = tokenParts[1]
    const base64 = payloadPart.replace(/-/g, "+").replace(/_/g, "/")
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=")
    const payload = JSON.parse(Buffer.from(padded, "base64").toString("utf8")) as { sub?: unknown }
    return typeof payload.sub === "string" && payload.sub.length > 0 ? payload.sub : undefined
  } catch {
    return undefined
  }
}

function enrichRunPayloadWithActor(body: string, authorizationHeader?: string): string {
  const actorId = decodeJwtSub(authorizationHeader)
  if (!actorId) {
    return body
  }

  try {
    const payload = JSON.parse(body) as {
      body?: { forwardedProps?: Record<string, unknown> }
    }

    if (!payload || typeof payload !== "object" || !payload.body || typeof payload.body !== "object") {
      return body
    }

    const forwardedPropsRaw = payload.body.forwardedProps
    const forwardedProps =
      forwardedPropsRaw && typeof forwardedPropsRaw === "object" && !Array.isArray(forwardedPropsRaw)
        ? { ...forwardedPropsRaw }
        : {}

    if (!forwardedProps.userId) {
      forwardedProps.userId = actorId
    }
    if (!forwardedProps.actorId) {
      forwardedProps.actorId = actorId
    }
    if (!forwardedProps.actor_id) {
      forwardedProps.actor_id = actorId
    }
    if (!forwardedProps.user_id) {
      forwardedProps.user_id = actorId
    }

    payload.body.forwardedProps = forwardedProps
    return JSON.stringify(payload)
  } catch {
    return body
  }
}

function getRequestBody(
  event: APIGatewayProxyEvent | APIGatewayProxyEventV2,
  authorizationHeader?: string
): BodyInit | undefined {
  if (!event.body) {
    return undefined
  }

  if (event.isBase64Encoded) {
    const decoded = Buffer.from(event.body, "base64")
    const decodedText = decoded.toString("utf8")
    const enriched = enrichRunPayloadWithActor(decodedText, authorizationHeader)
    return enriched === decodedText ? decoded : enriched
  }

  return enrichRunPayloadWithActor(event.body, authorizationHeader)
}

function createRuntimeResponseHandler() {
  const runtime = new CopilotRuntime({
    agents: {
      [agentName]: new HttpAgent({
        url: agentCoreAgUiUrl,
      }),
    } as never,
  })

  const serviceAdapter = new ExperimentalEmptyAdapter()

  return copilotRuntimeNodeHttpEndpoint({
    runtime,
    serviceAdapter,
    endpoint: endpointPath,
  })
}

function toOutputChunk(value: unknown): Buffer {
  if (typeof value === "string") {
    return Buffer.from(value, "utf8")
  }
  if (value instanceof Uint8Array) {
    return Buffer.from(value)
  }
  if (value instanceof ArrayBuffer) {
    return Buffer.from(new Uint8Array(value))
  }
  if (ArrayBuffer.isView(value)) {
    return Buffer.from(value.buffer, value.byteOffset, value.byteLength)
  }
  return Buffer.from(String(value), "utf8")
}

function getAwsLambdaRuntime() {
  const runtime = (globalThis as unknown as {
    awslambda?: {
      streamifyResponse: <TEvent>(
        handler: (event: TEvent, responseStream: unknown, context: Context) => Promise<void>
      ) => (event: TEvent, responseStream: unknown, context: Context) => Promise<void>
      HttpResponseStream: {
        from: (responseStream: unknown, metadata: StreamingResponseMetadata) => unknown
      }
    }
  }).awslambda

  if (!runtime) {
    throw new Error("AWS Lambda streaming runtime APIs are unavailable in this environment")
  }

  return runtime
}

function writeToStream(stream: StreamingWritable, chunk: Buffer): void {
  stream.write(chunk)
}

async function pipeResponseBody(response: Response, outputStream: StreamingWritable): Promise<number> {
  let bytesWritten = 0
  if (!response.body) {
    return bytesWritten
  }

  const reader = response.body.getReader() as ReadableStreamDefaultReader<unknown>
  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    const chunk = toOutputChunk(value)
    bytesWritten += chunk.byteLength
    writeToStream(outputStream, chunk)
  }
  return bytesWritten
}

async function streamHandler(
  event: APIGatewayProxyEvent | APIGatewayProxyEventV2,
  responseStream: unknown,
  _context: Context
): Promise<void> {
  const awsLambdaRuntime = getAwsLambdaRuntime()
  const headers = toHeaderRecord(event.headers)
  const requestOrigin = getHeaderValue(headers, "origin")
  const requestMethod = getRequestMethod(event).toUpperCase()
  const corsHeaders = buildCorsHeaders(requestOrigin)

  if (requestMethod === "OPTIONS") {
    const optionsStream = awsLambdaRuntime.HttpResponseStream.from(responseStream, {
      statusCode: 204,
      headers: corsHeaders,
    }) as StreamingWritable
    optionsStream.end()
    return
  }

  try {
    const requestUrl = getRequestUrl(event)
    const requestHeaders = new Headers(headers)
    const authorizationHeader = getHeaderValue(headers, "authorization")

    const request = new Request(requestUrl, {
      method: requestMethod,
      headers: requestHeaders,
      body:
        requestMethod === "GET" || requestMethod === "HEAD"
          ? undefined
          : getRequestBody(event, authorizationHeader),
    })

    const runtimeHandler = createRuntimeResponseHandler()
    const runtimeResponse = await runtimeHandler(request)

    if (!(runtimeResponse instanceof Response)) {
      throw new Error("CopilotKit runtime handler did not return a Response")
    }

    const responseHeaders: Record<string, string> = {
      ...corsHeaders,
    }
    runtimeResponse.headers.forEach((value, key) => {
      responseHeaders[key.toLowerCase()] = value
    })
    delete responseHeaders["content-length"]

    const outputStream = awsLambdaRuntime.HttpResponseStream.from(responseStream, {
      statusCode: runtimeResponse.status,
      headers: responseHeaders,
    }) as StreamingWritable

    const bytesWritten = await pipeResponseBody(runtimeResponse, outputStream)
    const contentType = runtimeResponse.headers.get("content-type")?.toLowerCase() ?? ""
    if (bytesWritten === 0 && contentType.includes("text/event-stream")) {
      // API Gateway streaming can return 502 for completely empty stream bodies.
      writeToStream(outputStream, Buffer.from(": keep-alive\n\n", "utf8"))
    }
    outputStream.end()
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    const stack = err instanceof Error ? err.stack : undefined
    console.error("[CopilotKit Lambda] Error:", message, stack ?? "")

    const errorStream = awsLambdaRuntime.HttpResponseStream.from(responseStream, {
      statusCode: 500,
      headers: {
        ...corsHeaders,
        "content-type": "application/json",
      },
    }) as StreamingWritable

    writeToStream(
      errorStream,
      Buffer.from(
        JSON.stringify({
          error: "CopilotKitRuntimeError",
          message,
          hint: "Check CloudWatch log group for this Lambda for full stack trace and upstream errors.",
        }),
        "utf8"
      )
    )
    errorStream.end()
  }
}

const awsLambdaRuntime = getAwsLambdaRuntime()
export const handler = awsLambdaRuntime.streamifyResponse(streamHandler)

function isApiGatewayV2(event: APIGatewayProxyEvent | APIGatewayProxyEventV2): event is APIGatewayProxyEventV2 {
  return (event as APIGatewayProxyEventV2).version === "2.0"
}
