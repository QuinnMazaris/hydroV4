"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { formatDistanceToNow } from "date-fns"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Send, Loader2, Bot, User, Clock, ChevronDown, ChevronRight, Wrench, CheckCircle2, XCircle, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { AppHeader } from "@/components/app-header"
import { Button } from "@/components/ui/button"

interface ToolCall {
  name: string
  arguments: any
  id?: string
}

interface ToolOutput {
  tool: string
  result: any
}

interface Message {
  id?: number
  source: "manual" | "automated"
  role: "user" | "assistant"
  content: string
  timestamp: Date
  toolCalls?: ToolCall[]
  toolOutputs?: ToolOutput[]
  ruleId?: string | null
  ruleName?: string | null
  pending?: boolean
}

// Component to render a single tool call with expandable details
function ToolCallCard({ call, output }: { call: ToolCall; output?: ToolOutput }) {
  const [isOpen, setIsOpen] = useState(false)
  
  const hasError = output?.result?.error || output?.result?.status === "error"
  const hasSuccess = output?.result?.status === "success" || output?.result?.processed > 0
  
  const getStatusIcon = () => {
    if (!output) return <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400" />
    if (hasError) return <XCircle className="h-3.5 w-3.5 text-red-400" />
    if (hasSuccess) return <CheckCircle2 className="h-3.5 w-3.5 text-green-400" />
    return <AlertCircle className="h-3.5 w-3.5 text-yellow-400" />
  }
  
  const getStatusColor = () => {
    if (!output) return "border-blue-500/30 bg-blue-500/10"
    if (hasError) return "border-red-500/30 bg-red-500/10"
    if (hasSuccess) return "border-green-500/30 bg-green-500/10"
    return "border-yellow-500/30 bg-yellow-500/10"
  }
  
  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <div className={cn("rounded-lg border", getStatusColor())}>
        <CollapsibleTrigger className="w-full">
          <div className="flex items-center gap-2 px-3 py-2 text-left">
            {isOpen ? (
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            )}
            <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="font-mono text-sm font-medium">{call.name}</span>
            <div className="ml-auto">{getStatusIcon()}</div>
          </div>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border-t border-white/10 px-3 py-2 space-y-2">
            {call.arguments && Object.keys(call.arguments).length > 0 && (
              <div>
                <div className="text-xs text-muted-foreground mb-1">Arguments:</div>
                <pre className="text-xs bg-black/40 rounded p-2 overflow-x-auto font-mono">
                  {JSON.stringify(call.arguments, null, 2)}
                </pre>
              </div>
            )}
            {output && (
              <div>
                <div className="text-xs text-muted-foreground mb-1">Result:</div>
                {renderToolResult(output)}
              </div>
            )}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}

// Render tool result with nice formatting
function renderToolResult(output: ToolOutput) {
  const { tool, result } = output
  
  // Control actuators - special formatting
  if (tool === 'control_actuators' && typeof result === 'object') {
    return (
      <div className="text-xs space-y-1 bg-black/40 rounded p-2">
        {result.processed > 0 && (
          <div className="flex items-center gap-1 text-green-400">
            <CheckCircle2 className="h-3 w-3" />
            <span>Processed: {result.processed} command(s)</span>
          </div>
        )}
        {result.skipped > 0 && (
          <div className="flex items-center gap-1 text-yellow-400">
            <AlertCircle className="h-3 w-3" />
            <span>Skipped: {result.skipped}</span>
          </div>
        )}
        {result.blocked?.length > 0 && (
          <div className="text-red-400">
            <div className="flex items-center gap-1">
              <XCircle className="h-3 w-3" />
              <span>Blocked:</span>
            </div>
            <ul className="list-disc list-inside ml-4 mt-1">
              {result.blocked.map((b: any, i: number) => (
                <li key={i}>{b.actuator_key}: {b.reason}</li>
              ))}
            </ul>
          </div>
        )}
        {result.dry_run && (
          <div className="text-blue-400 italic">
            (Dry run - no actual commands sent)
          </div>
        )}
      </div>
    )
  }
  
  // Sensor snapshot - show device readings
  if (tool === 'get_sensor_snapshot' && result?.devices) {
    const deviceCount = Object.keys(result.devices).length
    const metricCount = Object.values(result.devices).reduce(
      (acc: number, metrics: any) => acc + (Array.isArray(metrics) ? metrics.length : 0), 0
    )
    return (
      <div className="text-xs bg-black/40 rounded p-2">
        <div className="text-green-400 mb-1">
          Found {metricCount} readings from {deviceCount} device(s)
        </div>
        <pre className="overflow-x-auto font-mono opacity-70 max-h-32 overflow-y-auto">
          {JSON.stringify(result.devices, null, 2)}
        </pre>
      </div>
    )
  }
  
  // Error result
  if (result?.error || result?.status === "error") {
    return (
      <div className="text-xs bg-black/40 rounded p-2 text-red-400">
        <div className="flex items-center gap-1 mb-1">
          <XCircle className="h-3 w-3" />
          <span>Error</span>
        </div>
        <pre className="overflow-x-auto font-mono">
          {result.error || result.message || JSON.stringify(result, null, 2)}
        </pre>
      </div>
    )
  }
  
  // Default JSON display
  return (
    <pre className="text-xs bg-black/40 rounded p-2 overflow-x-auto font-mono max-h-40 overflow-y-auto">
      {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
    </pre>
  )
}

export default function ChatPage() {
  // Session-only state - clears when you leave the page
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeToolCalls, setActiveToolCalls] = useState<ToolCall[]>([])
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  
  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, activeToolCalls])

  const clearChat = useCallback(() => {
    setMessages([])
    setError(null)
    setActiveToolCalls([])
  }, [])

  const handleSend = async () => {
    if (!input.trim() || isLoading) return

    const userMessage: Message = {
      source: "manual",
      role: "user",
      content: input.trim(),
      timestamp: new Date(),
    }

    setMessages((prev) => [...prev, userMessage])
    setInput("")
    setIsLoading(true)
    setError(null)
    setActiveToolCalls([])

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
              .filter(m => m.role === "user" || m.role === "assistant")
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
              .filter(m => m.content && m.content.trim() !== ""),
            {
              role: "user",
              content: userMessage.content,
            },
          ],
          temperature: 0.3,
          max_iterations: 10,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`)
      }

      const data = await response.json()

      // Extract tool calls and outputs from trace
      const toolCalls: ToolCall[] = []
      const toolOutputs: ToolOutput[] = []

      if (Array.isArray(data.trace)) {
        data.trace.forEach((traceItem: any) => {
          const assistantBlock = traceItem?.assistant
          if (assistantBlock?.tool_calls) {
            assistantBlock.tool_calls.forEach((call: any) => {
              toolCalls.push({
                id: call?.id,
                name: call?.name ?? "unknown",
                arguments: call?.arguments ?? {},
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
        content: data.final || "",
        timestamp: new Date(),
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
        toolOutputs: toolOutputs.length > 0 ? toolOutputs : undefined,
      }

      setMessages((prev) => [...prev, assistantMessage])
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
        },
      ])
    } finally {
      setIsLoading(false)
      setActiveToolCalls([])
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
        <AppHeader 
          onClearChat={clearChat} 
          hasMessages={messages.length > 0} 
        />

        {/* Chat Container - Full Width */}
        <div className="flex-1 flex flex-col px-4 md:px-8 lg:px-12 py-6">
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
                            {message.toolOutputs.map((output, outIndex) => {
                              let content = null;
                              if (output.tool === 'control_actuators' && typeof output.result === 'object') {
                                const res = output.result as any;
                                content = (
                                  <div className="space-y-1">
                                    {res.processed > 0 && <div className="text-green-400">✓ Processed: {res.processed}</div>}
                                    {res.skipped > 0 && <div className="text-yellow-400">⚠ Skipped: {res.skipped}</div>}
                                    {res.blocked && res.blocked.length > 0 && (
                                      <div className="text-red-400">
                                        ✕ Blocked:
                                        <ul className="list-disc list-inside ml-1">
                                          {res.blocked.map((b: any, i: number) => (
                                            <li key={i}>{b.actuator_key}: {b.reason}</li>
                                          ))}
                                        </ul>
                                      </div>
                                    )}
                                    {res.missing && res.missing.length > 0 && (
                                      <div className="text-red-400">
                                        ✕ Missing:
                                        <ul className="list-disc list-inside ml-1">
                                          {res.missing.map((m: any, i: number) => (
                                            <li key={i}>{m.actuator_key} (not found)</li>
                                          ))}
                                        </ul>
                                      </div>
                                    )}
                                  </div>
                                );
                              } else {
                                content = (
                                  <div className="whitespace-pre-wrap opacity-80">
                                    {typeof output.result === 'string'
                                      ? output.result
                                      : JSON.stringify(output.result, null, 2)}
                                  </div>
                                );
                              }

                              return (
                                <div key={outIndex} className="text-xs bg-black/30 rounded p-2 font-mono overflow-x-auto border border-white/5">
                                  <div className="font-semibold mb-1 text-blue-300">{output.tool}</div>
                                  {content}
                                </div>
                              );
                            })}
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
                <div ref={messagesEndRef} />
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




