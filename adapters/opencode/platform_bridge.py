"""Replace executable dependencies without replacing original workflow logic."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


def install(bash, opencode=None, model=None):
    original_run = subprocess.run

    def run(args, *positional, **kwargs):
        if isinstance(args, (list, tuple)) and args:
            args = list(args)
            if args[0] == '/bin/bash':
                args[0] = bash
            elif args[0] == 'claude' and '-p' in args:
                if not opencode or not model:
                    raise FileNotFoundError('Configure the independent OpenCode adjudicator model')
                # No --session/--continue: preserve the original independent call.
                command = [opencode, 'run', '--format', 'json', '--model',
                           model['providerID'] + '/' + model['modelID'], '--', args[-1]]
                result = original_run(command, *positional, **kwargs)
                text = []
                for line in (result.stdout or '').splitlines():
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if event.get('type') == 'text' and isinstance(event.get('part', {}).get('text'), str):
                        text.append(event['part']['text'])
                return subprocess.CompletedProcess(command, result.returncode, '\n'.join(text), result.stderr)
        return original_run(args, *positional, **kwargs)

    subprocess.run = run
    return original_run


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    install(request['bash'], request.get('opencode'), request.get('model'))
    script = str(Path(request['script']).resolve())
    sys.path.insert(0, str(Path(script).parent))
    sys.argv = [script, *request.get('args', [])]
    os.chdir(request['directory'])
    if request.get('logDir') and Path(script).name == 'skill_runner.py':
        module = runpy.run_path(script, run_name='starfix_original_skill_runner')
        # Preserve the original canary functions, but move their shared persistent
        # output directory out of the source checkout. Do not reset it each run.
        module['main'].__globals__['LOG_DIR'] = request['logDir']
        sys.exit(module['main']())
    runpy.run_path(script, run_name='__main__')
