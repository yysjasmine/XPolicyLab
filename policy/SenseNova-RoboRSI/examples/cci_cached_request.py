#!/usr/bin/env python3
"""819/834 Responses requests with measured server-side prefix-cache reuse.

Python standard library only. No tools are executed. In prefix mode, a repeat
sends identical input to measure automatic prefix caching. Conversation mode
uses store/previous_response_id for server-managed history and owner routing;
only a new follow-up message is sent after the first request.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.request

MODELS = {
    819: "q38-base1012-half-embodied-200704-final-ck629",
    834: "q38-embodied-merged-final-ck447",
}
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def output_text(response):
    return "\n".join(
        part["text"]
        for item in response.get("output", []) if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") == "output_text"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, choices=MODELS, default=819)
    parser.add_argument("--host", default="10.210.6.10")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="A user message; very short prompts may have zero cached tokens")
    source.add_argument("--prompt-file", type=Path, help="UTF-8 text, preferably your actual long context")
    source.add_argument("--request-json", type=Path, help="Existing native CCI Responses payload, including images/tools")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--mode", choices=["prefix", "conversation"], default="prefix")
    parser.add_argument("--followup", default="Summarize the preceding material in one sentence.")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-output-tokens", type=int, default=65536)
    parser.add_argument("--save-dir", type=Path, help="Optional: save exact requests, raw JSON responses and receipts")
    parser.add_argument("--print-output", action="store_true")
    args = parser.parse_args(argv)
    if args.repeat < 1 or not 1 <= args.attempts <= 3 or args.timeout <= 0:
        parser.error("repeat/timeout must be positive; attempts must be 1..3")
    if not 1 <= args.max_output_tokens <= 65536:
        parser.error("max-output-tokens must be 1..65536")

    if args.request_json:
        payload = json.loads(args.request_json.read_text(encoding="utf-8"))
    else:
        text = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else args.text
        payload = {
            "instructions": "You are a helpful assistant.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
            "tools": [], "tool_choice": "auto", "parallel_tool_calls": False,
        }
    payload.update(
        model=MODELS[args.port], stream=False, store=args.mode == "conversation",
        reasoning={"effort": "medium", "context": "all_turns"},
        chat_template_kwargs={"enable_thinking": True, "reasoning_effort": "medium"},
        temperature=1.0, top_k=20, top_p=0.95,
        max_output_tokens=args.max_output_tokens,
    )
    # No unverified cache-control flags or changing timestamp/nonce in the input.
    # These deployed CCI services report automatic prefix reuse in cached_tokens.
    wire = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    url = f"http://{args.host}:{args.port}/v1/responses"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if args.save_dir:
        args.save_dir.mkdir(parents=True, exist_ok=False)
        (args.save_dir / "request.json").write_bytes(wire)
    total_input = total_cached = unknown_usage = 0
    previous_id = None
    for round_index in range(1, args.repeat + 1):
        if args.mode == "conversation" and round_index > 1:
            # Do not resend the old full history together with previous_response_id.
            payload["previous_response_id"] = previous_id
            payload["input"] = [{"role": "user", "content": [{"type": "input_text", "text": args.followup}]}]
            wire = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        for attempt in range(1, args.attempts + 1):
            started = time.monotonic()
            status = None
            raw = b""
            failure = None
            retryable = False
            router_backend = None
            try:
                request = urllib.request.Request(url, data=wire, headers={"Content-Type": "application/json"})
                with opener.open(request, timeout=args.timeout) as reply:
                    status = reply.status
                    router_backend = reply.headers.get("X-Router-Backend")
                    raw = reply.read()
            except urllib.error.HTTPError as error:
                status, raw = error.code, error.read()
                failure, retryable = f"HTTP {status}", status in RETRYABLE_HTTP
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as error:
                failure, retryable = str(error), True
            try:
                response = json.loads(raw) if raw else {}
            except ValueError:
                response = {}
                failure = failure or "Non-JSON response"
            usage = response.get("usage")
            input_tokens = usage.get("input_tokens", 0) if usage else None
            cached = ((usage.get("input_tokens_details") or {}).get("cached_tokens", 0)) if usage else None
            total_input += input_tokens or 0
            total_cached += cached or 0
            unknown_usage += usage is None
            failure = failure or (str(response["error"]) if response.get("error") else None)
            if not failure and response.get("status") != "completed":
                failure = f"Response status: {response.get('status')}; {response.get('incomplete_details')}"
            receipt = {
                "round": round_index, "attempt": attempt, "http_status": status,
                "mode": args.mode, "router_backend": router_backend,
                "response_status": response.get("status"), "elapsed_seconds": round(time.monotonic()-started, 3),
                "input_tokens": input_tokens, "cached_tokens": cached,
                "cache_hit_ratio": round(cached/input_tokens, 4) if input_tokens else None,
                "output_tokens": usage.get("output_tokens") if usage else None, "error": failure,
            }
            print(json.dumps(receipt, ensure_ascii=False), flush=True)
            if args.save_dir:
                prefix = args.save_dir / f"round-{round_index:02d}-attempt-{attempt:02d}"
                prefix.with_suffix(".request.json").write_bytes(wire)
                prefix.with_suffix(".response.json").write_bytes(raw)
                prefix.with_suffix(".receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            if not failure:
                if args.print_output:
                    print(output_text(response), flush=True)
                if args.mode == "conversation" and round_index < args.repeat:
                    if any(item.get("type") == "function_call" for item in response.get("output", [])):
                        raise RuntimeError("Return the required tool outputs before continuing this conversation; no tool was executed")
                    previous_id = response.get("id")
                    if not previous_id:
                        raise RuntimeError("Completed response has no id for conversation continuation")
                break
            if not retryable or attempt == args.attempts:
                raise RuntimeError(failure + "; retained evidence; no model/key/route fallback")
            time.sleep(min(2 ** (attempt-1), 4))
    print(json.dumps({"summary": {
        "completed_requests": args.repeat, "total_input_tokens": total_input,
        "total_cached_tokens": total_cached,
        "weighted_cache_hit_ratio": round(total_cached/total_input, 4) if total_input else None,
        "attempts_with_unknown_usage": unknown_usage,
    }}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
