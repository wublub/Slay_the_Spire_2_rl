"""Necrobinder 训练入口。"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.train import main


if __name__ == "__main__":
    main(default_character="Necrobinder", include_character_arg=False)
