#! /usr/bin/env -S python3 -u

import argparse
import os
import subprocess
import shlex

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

class Compiler:
    def __init__(self, bin_path, flags):
        self._args = [bin_path] +flags

    def output(self, source, *flags):
        cmd = self._args + list(flags) + [source]
        print(' '.join([shlex.quote(x) for x in cmd]))
        try:
            return subprocess.run(
                self._args + list(flags) + [source],
                check=True, capture_output=True
            ).stdout.decode('utf-8')
        except subprocess.CalledProcessError as e:
            print(e.stderr.decode('utf-8'))

def main():
    parser = argparse.ArgumentParser(description='Compiler Profiler')
    parser.add_argument('--bin', type=str, default='g++', help='c++ compiler path')
    parser.add_argument('--flags', type=str, help='compiler flags')
    subparsers = parser.add_subparsers(dest='mode', required=True)
    subparser = subparsers.add_parser('header')
    subparser.add_argument('paths', nargs='+')

    args = parser.parse_args()
    cc = Compiler(args.bin, shlex.split(args.flags) if args.flags else [])
    sources = find_sources(args.paths, ['cpp'])
    for source in sources:
        print(cc.output(source, '-H'))

main()
