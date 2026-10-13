"""python -m cameo [run|targets|act|doctor|pack|unpack]  (ARCHITECTURE.md §5.7, §5.8). Windows and Linux (X11)."""
import argparse
import json
import os
import sys

from .stdio import ensure_streams

ensure_streams()  # before any native library loads

from .contracts import Intent, failure, to_json
from .control.browser import BridgeServer, BrowserController
from .control.windows import WindowBackend
from .eventlog import EventLog
from .targets import TargetRegistry
from . import doctor


def resolve(registry, text):
    if text == 'active':
        target = registry.active()
        return [target] if target else []
    if text.startswith('win:0x'):
        target = registry.get(text)
        return [target] if target else []
    return registry.find(text)


def packages(args):
    """`pack` saves a .cameo file; `unpack` puts one's contents into the inbox, sorted like a drop from a device."""
    import secrets
    import shutil
    from pathlib import Path
    from .link.package import PackageError, install_into, pack_file, unpack_file
    from .paths import inbox_dir
    try:
        if args.command == 'pack':
            packer = pack_file(args.paths, args.out)
            print(f'{args.out}: {packer.count} file(s), {packer.size} bytes')
            return 0
        inbox = inbox_dir()
        staging = inbox / '.staging' / ('open-' + secrets.token_hex(4))
        try:
            unpacker = unpack_file(args.package, staging)
            placed = install_into(unpacker, inbox)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except (PackageError, OSError, ValueError) as error:
        print(f'cameo {args.command}: {error}', file=sys.stderr)
        if args.command == 'unpack' and os.path.basename(sys.executable).lower().startswith('pythonw'):  # opened from Explorer: no console
            from .control.system import message
            message(f'This .cameo package could not be opened:\n{error}')
        return 1
    for path in placed:
        print(path)
    if placed and not args.no_open:
        from .control.system import open_path
        open_path(placed[0] if len(placed) == 1 and Path(placed[0]).is_dir() else Path(placed[0]).parent)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='cameo')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('targets', help='list controllable windows')
    act = sub.add_parser('act', help='perform one action on a real target and print the evidence')
    act.add_argument('action')
    act.add_argument('--target', default='active', help="'active', a window id (win:0x...), or an app/title word")
    sub.add_parser('doctor', help='check devices, models and platform')
    run = sub.add_parser('run', help='start Cameo (HUD, voice, hands)')
    run.add_argument('--no-voice', action='store_true')
    run.add_argument('--no-hands', action='store_true')
    run.add_argument('--camera', type=int, default=0)
    run.add_argument('--gestures', choices=('classic', 'dock'), default='classic', help="'classic' = your signs, 'dock' = the plan's pinch dock")
    run.add_argument('--no-link', action='store_true', help='no paired devices (grab and drop stays on this computer)')
    run.add_argument('--trace', action='store_true', help='record every hand frame for python -m cameo replay (about 10 KB/s)')
    sub.add_parser('signs', help="print every hand sign and its rule (the table in GESTURES.md)")
    replay = sub.add_parser('replay', help='replay a recorded session through the current sign engine and score it')
    replay.add_argument('session', help='session folder, or its name under the Cameo sessions folder (e.g. 20260928-131444)')
    replay.add_argument('--window', type=float, default=2.0, help='seconds after a label within which the sign must fire')
    replay.add_argument('--labels', help='labels file (default: labels.jsonl next to the session log)')
    pack = sub.add_parser('pack', help='save files and folders as one .cameo package')
    pack.add_argument('paths', nargs='+')
    pack.add_argument('-o', '--out', required=True)
    unpack = sub.add_parser('unpack', help='unpack a .cameo package into the Cameo inbox (what opening a .cameo file does)')
    unpack.add_argument('package')
    unpack.add_argument('--no-open', action='store_true', help='do not show the result')
    args = parser.parse_args(argv)
    if sys.stdout is not None:  # pythonw (the launcher) has no console
        sys.stdout.reconfigure(encoding='utf-8')

    if args.command == 'signs':
        from .gestures.catalog import markdown
        print(markdown())
        return 0

    if args.command == 'replay':
        from .replay import report
        text, code = report(args.session, args.window, args.labels)
        print(text)
        return code

    if args.command == 'run':
        if args.trace:
            os.environ['CAMEO_TRACE'] = '1'
        platform = doctor.session()
        if platform['status'] == 'red':
            print(f"Cameo cannot control this desktop: {platform['detail']}", file=sys.stderr)
            from .control.system import message
            message(f"Cameo cannot control this desktop.\n{platform['detail']}")
            return 1
        from .main import run
        try:
            run(voice=not args.no_voice, hands=not args.no_hands, camera=args.camera, gestures=args.gestures, link=not args.no_link)
        except PermissionError:
            from .control.system import message
            message('Cameo is already running. Use its rail, or press Ctrl+Alt+Q to quit it first.')
            return 1
        return 0

    if args.command in ('pack', 'unpack'):
        return packages(args)

    if args.command == 'doctor':
        results = doctor.run()
        for check in results:
            print(f"{check['status']:5} {check['name']:22} {check['detail']}")
        return 0 if all(check['status'] != 'red' for check in results) else 1

    registry = TargetRegistry()
    if args.command == 'targets':
        for target in registry.list():
            state = ','.join(key for key, value in target.state.items() if value) or 'normal'
            print(f'{target.id}  {target.kind:14} {target.app:18} [{state}]  {target.title[:70]}')
        return 0

    log = EventLog(live=True)
    if args.action.startswith('tab.'):
        bridge = BridgeServer()
        print('waiting for the browser bridge...', file=sys.stderr)
        bridge.wait_connected(10)
        intent = Intent(args.action, None, 'default')
        log.write(intent)
        result = BrowserController(bridge, registry).act(intent)
        log.write(result)
        print(json.dumps(to_json(result), indent=2))
        bridge.close()
        return 0 if result.status == 'verified' else 1
    matches = resolve(registry, args.target)
    intent = Intent(args.action, matches[0].id if len(matches) == 1 else None, 'explicit')
    log.write(intent)
    if len(matches) != 1:
        result = failure(intent.intent_id, 'TARGET_AMBIGUOUS' if matches else 'TARGET_GONE',
                         f'{len(matches)} windows match {args.target!r}', 'Use a window id from `aether targets`')
    else:
        result = WindowBackend(registry).act(intent)
    log.write(result)
    print(json.dumps(to_json(result), indent=2))
    return 0 if result.status == 'verified' else 1


if __name__ == '__main__':
    sys.exit(main())
