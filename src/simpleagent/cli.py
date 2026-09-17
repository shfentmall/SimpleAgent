"""命令行入口：sa。"""

from __future__ import annotations

import argparse
import sys

from simpleagent.config import ConfigError, init_config, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sa", description="SimpleAgent：自用、可学习的本地 agent")
    parser.add_argument("-m", "--profile", help="模型 profile，默认取配置里的 default_profile")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("init", help="生成默认配置文件 ~/.simpleagent/config.toml")
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            print(f"已生成配置：{init_config()}")
            return 0
        from simpleagent.ui.repl import Repl  # 延迟导入：init 不需要加载 openai

        return Repl(load_config(), profile=args.profile).run()
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
