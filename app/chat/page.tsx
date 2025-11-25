"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { formatDistanceToNow } from "date-fns"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Send, Loader2, Bot, User, Clock } from "lucide-react"
import { cn } from "@/lib/utils"

interface Message {
  id?: number
  source: "manual" | "automated"
  role: "user" | "assistant"
  content: string
  timestamp: Date
  toolCalls?: Array<{
    name: string
    arguments: any
  }>
  toolOutputs?: Array<{
    tool: string
    result: any
  }>
  ruleId?: string | null
  ruleName?: string | null
  pending?: boolean
}

const signatureKey = (message: Pick<Message, "source" | "role" | "content">) =>
  `${message.source}:${message.role}:${message.content}`

const mapApiMessage = (payload: any): Message => {
  // Parse timestamp as UTC by appending 'Z' if not present
  let timestamp = new Date()
  if (payload?.timestamp) {
    const ts = payload.timestamp
    timestamp = new Date(ts.endsWith('Z') ? ts : ts + 'Z')
  }
  return {
    id: payload?.id,
    source: payload?.source === "automated" ? "automated" : "manual",
    role: payload?.role === "user" ? "user" : "assistant",
    content: payload?.content ?? "",
    timestamp,
    toolCalls: Array.isArray(payload?.tool_calls) ? payload.tool_calls : undefined,
    toolOutputs: payload?.message_meta?.trace
      ? payload.message_meta.trace.flatMap((step: any) => step.tools || [])
      : undefined,
    ruleId: payload?.rule_id ?? null,
    ruleName: payload?.rule_name ?? null,
  }
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const latestTimestampRef = useRef<Date | null>(null)
  const pollingRef = useRef<NodeJS.Timeout | null>(null)

  const mergeMessages = useCallback((incoming: Message[], replace: boolean) => {
    setMessages((prev) => {
      const base = replace ? [] : prev

      const persistedMap = new Map<number, Message>()
      const pending: Message[] = []

      for (const message of base) {
        if (message.id !== undefined) {
          persistedMap.set(message.id, message)
        } else {
          pending.push(message)
        }
      }

      const incomingSignatures = new Set<string>()

      for (const message of incoming) {
        if (message.id !== undefined) {
          persistedMap.set(message.id, message)
        }
        incomingSignatures.add(signatureKey(message))
      }

      const filteredPending = pending.filter(
        (message) => !incomingSignatures.has(signatureKey(message))
      )

      const newPending = incoming.filter((message) => message.id === undefined)

      const merged = [...persistedMap.values(), ...filteredPending, ...newPending]
      merged.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime())
      return merged
    })

    if (incoming.length > 0) {
      latestTimestampRef.current = incoming[incoming.length - 1].timestamp
    }
  }, [])

  const fetchMessages = useCallback(
    async (since?: Date) => {
      try {
        const params = new URLSearchParams({ limit: "100" })
        if (since) {
          params.set("since", since.toISOString())
        }

        const response = await fetch(`/api/conversations?${params.toString()}`)
        if (!response.ok) {
          throw new Error(`Failed to load conversations (${response.status})`)
        }

        const payload = await response.json()
        const mapped: Message[] = Array.isArray(payload) ? payload.map(mapApiMessage) : []
        mergeMessages(mapped, !since)
        setError(null)
      } catch (err) {
        console.error("Failed to fetch conversation history", err)
        setError((err as Error)?.message ?? "Failed to load conversation history")
      }
    },
    [mergeMessages]
  )

  useEffect(() => {
    fetchMessages()
  }, [fetchMessages])

  useEffect(() => {
    pollingRef.current = setInterval(() => {
      const since = latestTimestampRef.current
      fetchMessages(since ?? undefined)
    }, 5000)

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
      }
    }
  }, [fetchMessages])

  const handleSend = async () => {
    if (!input.trim() || isLoading) return

    const userMessage: Message = {
      source: "manual",
      role: "user",
      content: input.trim(),
      timestamp: new Date(),
      pending: true,
    }

    setMessages((prev) => [...prev, userMessage])
    setInput("")
    setIsLoading(true)
    setError(null)

    try {
      const gardenerPort = "8600"
      const gardenerUrl = `http://${window.location.hostname}:${gardenerPort}`

      const response = await fetch(`${gardenerUrl}/agent/run`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: [
            ...messages
              .map((m) => {
                // Synthesize content if empty but tools were used
                if ((!m.content || !m.content.trim()) && m.toolCalls && m.toolCalls.length > 0) {
                  return {
                    role: m.role,
                    content: `(Executed tools: ${m.toolCalls.map(t => t.name).join(', ')})`
                  }
                }
                return { role: m.role, content: m.content }
              })
              .filter(m => m.content && m.content.trim() !== ""), // Filter out completely empty messages
            {
              role: "user",
              content: userMessage.content,
            },
          ],
          temperature: 0.3,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`)
      }

      const data = await response.json()

      const toolCalls: Array<{ name: string; arguments: any }> = []
      const toolOutputs: Array<{ tool: string; result: any }> = []

      if (Array.isArray(data.trace)) {
        data.trace.forEach((traceItem: any) => {
          const assistantBlock = traceItem?.assistant
          if (assistantBlock?.tool_calls) {
            assistantBlock.tool_calls.forEach((call: any) => {
              toolCalls.push({
                name: call?.name ?? "unknown",
                arguments: call?.arguments,
              })
            })
          }
          if (traceItem?.tools) {
            traceItem.tools.forEach((output: any) => {
              toolOutputs.push(output)
            })
          }
        })
      }

      const assistantMessage: Message = {
        source: "manual",
        role: "assistant",
        content: data.final || "I'm processing your request...",
        timestamp: new Date(),
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
        toolOutputs: toolOutputs.length > 0 ? toolOutputs : undefined,
        pending: true,
      }

      setMessages((prev) => [...prev, assistantMessage])
      fetchMessages(latestTimestampRef.current ?? undefined)
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Failed to get response from AI agent"
      setError(errorMessage)
      setMessages((prev) => [
        ...prev,
        {
          source: "manual",
          role: "assistant",
          content: `Sorry, I encountered an error: ${errorMessage}. Please make sure the Gardener service is running on port 8600.`,
          timestamp: new Date(),
          pending: true,
        },
      ])
    } finally {
      setIsLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden text-foreground">
      <div className="absolute inset-0">
        <img src="/bg.jpeg" alt="" className="h-full w-full object-cover" aria-hidden="true" />
        <div className="absolute inset-0 bg-black/50" aria-hidden="true" />
      </div>

      <div className="relative z-10 flex min-h-screen flex-col">
        {/* Header */}
        <header className="border-b border-white/10 bg-black/30 backdrop-blur-md">
          <div className="container mx-auto px-6 py-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-4">
                <div className="flex items-center space-x-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
                    <Bot className="h-5 w-5 text-primary-foreground" />
                  </div>
                  <h1 className="text-xl font-semibold text-foreground">AI Gardener Chat</h1>
                </div>
                <Badge variant="secondary" className="text-xs backdrop-blur">
                  AI Assistant
                </Badge>
              </div>
              <a href="/">
                <Button variant="ghost" size="sm">
                  Back to Dashboard
                </Button>
              </a>
            </div>
          </div>
        </header>

        {/* Chat Container */}
        <div className="container mx-auto flex-1 flex flex-col px-6 py-8 max-w-4xl">
          <Card className="flex-1 flex flex-col border-white/10 bg-black/55 backdrop-blur-xl min-h-0">
            <CardHeader>
              <CardTitle>Chat with Gardener</CardTitle>
              <p className="text-sm text-muted-foreground">
                Ask about sensor readings, system status, historical trends, or request actuator adjustments.
              </p>
            </CardHeader>
            <CardContent className="flex-1 flex flex-col min-h-0 p-0">
              {/* Messages Area */}
              <div className="flex-1 overflow-y-auto p-6 space-y-4">
                {messages.length === 0 && (
                  <div className="rounded-lg border border-white/10 bg-black/40 p-4 text-sm text-muted-foreground">
                    No conversations yet. Ask a question to start chatting with Gardener.
                  </div>
                )}
                {messages.map((message, index) => (
                  <div
                    key={index}
                    className={cn(
                      "flex gap-4",
                      message.role === "user" ? "justify-end" : "justify-start"
                    )}
                  >
                    {message.role === "assistant" && (
                      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/20 shrink-0">
                        <Bot className="h-4 w-4 text-primary" />
                      </div>
                    )}
                    <div
                      className={cn(
                        "rounded-lg px-4 py-3 max-w-[80%]",
                        message.role === "user"
                          ? "bg-primary text-primary-foreground"
                          : message.source === "automated"
                            ? "bg-blue-500/10 text-foreground backdrop-blur border border-blue-500/20"
                            : "bg-white/10 text-foreground backdrop-blur"
                      )}
                    >
                      <div className="flex items-center gap-2">
                        {message.source === "automated" && message.role === "assistant" && (
                          <Badge variant="outline" className="text-xs flex items-center gap-1 border-blue-500/50 text-blue-200">
                            <Clock className="h-3 w-3" /> Automated
                            {message.ruleName && <span>· {message.ruleName}</span>}
                          </Badge>
                        )}
                        {message.pending && (
                          <Badge variant="secondary" className="text-xs">
                            Pending
                          </Badge>
                        )}
                      </div>
                      <div className="whitespace-pre-wrap mt-1">{message.content}</div>
                      {message.toolCalls && message.toolCalls.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-white/10">
                          <div className="text-xs text-muted-foreground mb-1">Tools used:</div>
                          <div className="flex flex-wrap gap-1">
                            {message.toolCalls.map((tool, toolIndex) => (
                              <Badge key={toolIndex} variant="outline" className="text-xs">
                                {tool.name}
                              </Badge>
                            ))}
                          </div>
                        </div>
                      )}
                      {message.toolOutputs && message.toolOutputs.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-white/10">
                          <div className="text-xs text-muted-foreground mb-1">Tool Outputs:</div>
                          <div className="space-y-2">
                            {message.toolOutputs.map((output, outIndex) => (
                              <div key={outIndex} className="text-xs bg-black/30 rounded p-2 font-mono overflow-x-auto border border-white/5">
                                <div className="font-semibold mb-1 text-blue-300">{output.tool}</div>
                                <div className="whitespace-pre-wrap opacity-80">
                                  {typeof output.result === 'string'
                                    ? output.result
                                    : JSON.stringify(output.result, null, 2)}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      <div className="text-xs text-muted-foreground mt-2">
                        {formatDistanceToNow(message.timestamp, { addSuffix: true })}
                      </div>
                    </div>
                    {message.role === "user" && (
                      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/20 shrink-0">
                        <User className="h-4 w-4 text-primary" />
                      </div>
                    )}
                  </div>
                ))}
                {isLoading && (
                  <div className="flex gap-4 justify-start">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/20 shrink-0">
                      <Bot className="h-4 w-4 text-primary" />
                    </div>
                    <div className="rounded-lg px-4 py-3 bg-white/10 backdrop-blur">
                      <div className="flex items-center gap-2">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        <span className="text-sm text-muted-foreground">Thinking...</span>
                      </div>
                    </div>
                  </div>
                )}
                {error && (
                  <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                  </div>
                )}

              </div>

              {/* Input Area */}
              <div className="border-t border-white/10 p-4">
                <div className="flex gap-2">
                  <Textarea
                    ref={textareaRef}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Ask about sensors, request adjustments, or analyze trends..."
                    className="min-h-[80px] resize-none bg-black/40 border-white/20 backdrop-blur"
                    disabled={isLoading}
                  />
                  <Button
                    onClick={handleSend}
                    disabled={!input.trim() || isLoading}
                    className="shrink-0"
                  >
                    {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground mt-2">
                  Press Enter to send, Shift+Enter for new line
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}




