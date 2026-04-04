"""启动本地 JSONL bridge server。"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bridge_server import BridgeServer
from agent.model_paths import CHARACTERS



def main():
    parser = argparse.ArgumentParser(description="启动 STS Agent 本地桥接服务")
    parser.add_argument("--preload-all", action="store_true", help="启动时预加载全部角色模型")
    parser.add_argument("--preload", nargs="*", choices=CHARACTERS, default=None, help="预加载指定角色模型")
    args = parser.parse_args()

    server = BridgeServer()
    preload_characters = []
    if args.preload_all:
        preload_characters = list(CHARACTERS)
    elif args.preload:
        preload_characters = list(args.preload)

    if preload_characters:
        server.preload(preload_characters)

    server.serve_forever(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
