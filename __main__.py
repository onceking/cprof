#! /usr/bin/env -S python3 -u

import collections
import json
import argparse
import os
import subprocess
import shlex
import tempfile
import re
import hashlib
import pickle

import anytree

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

def print_cmd(cmd):
    print(' '.join([shlex.quote(x) for x in cmd]))

def timed_run(cmd, **kwargs):
    tmpfile = tempfile.NamedTemporaryFile('r')
    print_cmd(cmd)
    cmd = [
        '/usr/bin/time',
        '--output', tmpfile.name,
        '--format', re.sub(r': "(%.)"', r': \1', json.dumps({
            'status': '%x',
            'wall': '%e',
            'sys': '%S',
            'user': '%U',
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
    js = {'status': -1}
    if proc.returncode == 0:
        js = json.load(tmpfile)
        js['cpu'] = js['sys'] + js['user']
    return proc, js

def atomic_write(path, content):
    dirname = os.path.dirname(path)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    tmpname = '%s.tmp' %(path)
    with open(tmpname, 'wb') as outf:
        pickle.dump(content, outf)
    os.rename(tmpname, path)

def cached(func):
    def wrapper(obj, *args):
        ret = None
        cache = None
        if obj.cache_dir:
            hsh = hashlib.sha256(b'v1')
            hsh.update(func.__name__.encode('utf-8'))
            for arg in args:
                hsh.update(arg.encode('utf-8'))
            hdigest = hsh.hexdigest()
            cache = os.path.join(obj.cache_dir, hdigest[0], hdigest)
        if cache and os.path.exists(cache):
            ret = pickle.load(open(cache, 'rb'))
        else:
            ret = func(obj, *args)
            if cache:
                atomic_write(cache, ret)
        return ret
    return wrapper

class Header:
    def __init__(self):
        self.count = 0
        self.failed = None
        self.time = None

class Compiler:
    def __init__(self, bin_path, flags):
        self._args = [bin_path] +flags
        self.cache_dir = None

    def _cmd(self, source, *flags):
        cmd = self._args + list(flags) + [source]
        return cmd

    def includes(self, sources,  *flags):
        root = anytree.Node('root', header=Header())
        headers = {}
        for source in sources:
            sroot = anytree.Node(source, parent=root, header=Header())
            stack = []
            seen = set()
            for l in self.stderr(source,'-E', '-H', *flags).split('\n'):
                # invalid precompiled header file is printed with ‘...x’
                #                            and a valid one with ‘...!’

                m = re.search(r'^([.]+) (.+)', l.strip())
                if not m:
                    continue
                level = len(m.group(1))
                path = m.group(2)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                if path not in headers:
                    headers[path] = Header()
                if path not in seen:
                    headers[path].count += 1
                    seen.add(path)
                node = anytree.Node(
                    m.group(2),
                    parent=stack[-1][1] if stack else sroot,
                    header=headers[path])
                stack.append((level, node))
        return root, headers

    def stdout(self, source, *flags):
        return self._output('stdout', source, *flags)
    def stderr(self, source, *flags):
        return self._output('stderr', source, *flags)

    @cached
    def _output(self, which, source, *flags):
        try:
            cmd = self._cmd(source, *flags)
            print_cmd(cmd)
            proc = subprocess.run(cmd, check=True, capture_output=True)
            s = proc.stderr if which == 'stderr' else proc.stdout
            return s.decode('utf-8')
        except subprocess.CalledProcessError as e:
            print(e.stderr.decode('utf-8'))

    def time(self, source, *flags):
        return timed_run(self._cmd(source, *flags))[1]

    @cached
    def time_header(self, header, *flags):
        tmpfile = tempfile.NamedTemporaryFile(
            'wb', suffix='.cpp', buffering=0)
        tmpfile.write(f'#include <{header}>'.encode('utf-8'))
        return self.time(tmpfile.name, '-c', *flags)


def main():
    parser = argparse.ArgumentParser(description='Compiler Profiler')
    parser.add_argument('--bin', type=str, default='g++', help='c++ compiler path')
    parser.add_argument('--flags', type=str, help='compiler flags')
    parser.add_argument('--cache-dir', type=str)
    subparsers = parser.add_subparsers(dest='mode', required=True)
    subparser = subparsers.add_parser('header')
    subparser.add_argument('--min-refs', type=int, default=2)
    subparser.add_argument('--min-duration', type=float, default=0.1)
    subparser.add_argument('paths', nargs='+')

    args = parser.parse_args()
    cc = Compiler(
        args.bin,
        (shlex.split(args.flags) if args.flags else []))
    cc.cache_dir = args.cache_dir
    sources = find_sources(args.paths, ['cpp'])
    # wall_times = {}
    # for source in sources:
    #     t = cc.time(source, '-c')
    #     assert t['status'] == 0
    #     wall_times[source] = t['cpu']
    # print(wall_times)

    htree, headers = cc.includes(sources)
    for hnode in anytree.LevelOrderIter(htree):
        if hnode.header.count < args.min_refs or \
           hnode.header.time:
            continue

        if any([
                x.header.time \
                and x.header.time['status'] == 0 \
                and x.header.time['cpu'] < args.min_duration
                for x in hnode.ancestors]):
            continue
        hnode.header.time = cc.time_header(hnode.name)

    # headers_by_time = sorted(
    #     [
    #         x for x, y in headers.items()
    #         if (y.time and
    #             y.time['status'] == 0 and
    #             y.time['cpu'] >= args.min_duration)
    #     ],
    #     key=lambda x: headers[x].time['cpu'] * headers[x].count,
    #     reverse=True)
    printed = set()
    for hnode in anytree.LevelOrderIter(htree):
        if hnode.name not in printed and \
           hnode.header.time and \
           hnode.header.time['status'] == 0 and \
           hnode.header.time['cpu'] >= args.min_duration:
            printed.add(hnode.name)
            print('%d %d x %0.1fs: %s' %(
                hnode.depth,
                hnode.header.count,
                hnode.header.time['cpu'],
                hnode.name))

main()
