#! /usr/bin/env -S python3 -u

import collections
import json
import argparse
import os
import subprocess
import shlex
import tempfile
import re

def find_sources(paths, extensions):
    sources = set()
    for path in paths:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            if path.rsplit('.')[-1].lower() in extensions:
                source.add(path)
            continue
        for curdir, _, files in os.walk(path):
            for f in files:
                if f.rsplit('.')[-1].lower() in extensions:
                    sources.add(os.path.join(curdir, f))
    return sorted(sources)

def timed_run(cmd, **kwargs):
    tmpfile = tempfile.NamedTemporaryFile('r')
    cmd = [
        '/usr/bin/time',
        '--output', tmpfile.name,
        '--format', re.sub(r': "(%.)"', r': \1', json.dumps({
            'status': '%x',
            'time': {
                'wall': '%e',
                'sys': '%S',
                'user': '%U'
            },
            'fault': {
                'major': '%F',
                'minor': '%R',
            },
            'rss': {
                'max': '%M',
            }
        }))
    ] + cmd
    proc = subprocess.run(cmd, **kwargs)
    return proc, json.load(tmpfile)

class Compiler:
    def __init__(self, bin_path, flags):
        self._args = [bin_path] +flags

    def _cmd(self, source, *flags):
        cmd = self._args + list(flags) + [source]
        print(' '.join([shlex.quote(x) for x in cmd]))
        return cmd

    def includes(self, source, *flags):
        # invalid precompiled header file is printed with ‘...x’ and a valid one with ‘...!’ .
        ret = set()
        for l in self.stderr(source,'-E', '-H', *flags).split('\n'):
            m = re.search(r'^[.]+ (.+)', l.strip())
            if m:
                ret.add(m.group(1))
        return ret

    def stdout(self, source, *flags):
        return self._output('stdout', source, *flags)
    def stderr(self, source, *flags):
        return self._output('stderr', source, *flags)
    def _output(self, which, source, *flags):
        try:
            proc = subprocess.run(
                self._cmd(source, *flags),
                check=True, capture_output=True
            )
            s = (proc.stderr if which == 'stderr' else proc.stdout)
            return s.decode('utf-8')
        except subprocess.CalledProcessError as e:
            print(e.stderr.decode('utf-8'))

    def time(self, source, *flags):
        return timed_run(self._cmd(source, *flags))[1]

def main():
    parser = argparse.ArgumentParser(description='Compiler Profiler')
    parser.add_argument('--bin', type=str, default='g++', help='c++ compiler path')
    parser.add_argument('--flags', type=str, help='compiler flags')
    subparsers = parser.add_subparsers(dest='mode', required=True)
    subparser = subparsers.add_parser('header')
    subparser.add_argument('paths', nargs='+')

    args = parser.parse_args()
    cc = Compiler(
        args.bin,
        (shlex.split(args.flags) if args.flags else []))
    sources = find_sources(args.paths, ['cpp'])
    # wall_times = {}
    # for source in sources:
    #     t = cc.time(source, '-c')
    #     assert t['status'] == 0
    #     wall_times[source] = t['time']['wall']
    # print(wall_times)

    headers = collections.defaultdict(int)
    for source in sources:
        for header in cc.includes(source):
            headers[header] += 1
    print({x: y for x,y in headers.items() if y == len(sources)})

main()
