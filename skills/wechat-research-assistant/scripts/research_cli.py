"""Versioned JSON interface for Codex and other local callers."""
import argparse
import json
import sys

import public_search
import workflow
import investigation_tasks
import chat_projects
import chat_library


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    handlers = {'wechat': workflow.main, 'research': public_search.main, 'task': investigation_tasks.main,
                'project': chat_projects.main, 'library': chat_library.main}
    parser = argparse.ArgumentParser(prog='research_cli', description=__doc__)
    parser.add_argument('group', choices=handlers, help='wechat: selected chats; research: sources and reports; task: Codex investigations')
    if not args or args[0] in ('-h', '--help'):
        parser.print_help()
        return 0
    group = parser.parse_args(args[:1]).group
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8-sig')
    command = args[1] if len(args) > 1 else '--help'
    emitted = []
    exit_code = handlers[group](args[1:] or ['--help'], emit=emitted.append)
    if emitted:
        print(json.dumps({'version': 1, 'command': group + '.' + command, 'ok': exit_code == 0,
                          'data': emitted[0]}, ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
