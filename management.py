"""Run project commands: python management.py <command> [options]

Every module in commands/ is a command, named after its file. A command module defines:
    help                    one-line description shown in --help
    add_arguments(parser)   optional, adds the command's argparse arguments
    handle(args)            runs the command
"""
import argparse
import importlib
import pkgutil

import commands


def load_commands():
    found = {}
    for module_info in pkgutil.iter_modules(commands.__path__):
        if module_info.ispkg or module_info.name.startswith("_"):
            continue
        found[module_info.name] = importlib.import_module(f"commands.{module_info.name}")
    return found


def main():
    parser = argparse.ArgumentParser(prog="management.py")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    for name, module in sorted(load_commands().items()):
        sub = subparsers.add_parser(name, help=getattr(module, "help", ""))
        if hasattr(module, "add_arguments"):
            module.add_arguments(sub)
        sub.set_defaults(handle=module.handle)

    args = parser.parse_args()
    args.handle(args)


if __name__ == "__main__":
    main()
