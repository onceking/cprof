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
    if dirname and not os.path.exists(dirname):
        os.makedirs(dirname)
    tmpname = '%s.tmp' %(path)
    with open(tmpname, 'wb') as outf:
        outf.write(content.encode('utf-8') if isinstance(content, str) else content)
    os.chmod(tmpname, 0o666)
    os.rename(tmpname, path)

def write_csv(path, header, rows):
    s = ','.join(header) + '\n'
    for row in rows:
        s += ','.join(row) + '\n'
    atomic_write(path + '.csv', s)

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
                atomic_write(cache, pickle.dumps(ret))
        return ret
    return wrapper

def filter_up(node, func):
    if func(node):
        return True
    for i in node.ancestors:
        if func(i):
            return True
    return False

class Header:
    def __init__(self):
        self.count = 0
        self.time = None

    def ok(self):
        return self.time and self.time['status'] == 0

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
    subparser.add_argument('--common-pct', type=float, default=90)
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
    if args.mode == 'header':
        act_header(cc, sources, args)


def write_header_csv(name, headers, keys):
    if not keys:
        return

    write_csv(
        f'header.{name}',
        ['count', 'time', 'tot', 'path'],
        [
            (
                str(headers[x].count),
                '%0.1f' %(headers[x].time['cpu']),
                '%0.1f' %(headers[x].time['cpu'] * headers[x].count),
                x
            )
            for x in sorted(keys, key=lambda x: -headers[x].time['cpu'] * headers[x].count)
        ])

def act_header(cc, sources, args):
    htree, headers = cc.includes(sources)
    for node in anytree.LevelOrderIter(htree):
        if node.header.count < args.min_refs or \
           node.header.time:
            continue

        if filter_up(node, lambda x: x.header.ok() and x.header.time['cpu'] < args.min_duration):
            continue
        node.header.time = cc.time_header(node.name)

    # full
    content = ''
    for node in anytree.PreOrderIter(htree):
        if not node.parent:
            continue
        if not node.parent.parent:
            content += node.name + '\n'
            continue

        if not node.header.ok():
            continue

        iter = anytree.LevelOrderGroupIter(node)
        children = []
        try:
            next(iter)
            children = next(iter)
        except StopIteration:
            pass
        time_self = max(
            0,
            node.header.time['cpu'] - sum([
                child.header.time['cpu'] if child.header.ok() else 0
                for child in children
            ]))
        time_total = node.header.time['cpu']

        content += '%s%0.1f %0.1f %s\n' % (' '*(node.depth-1), time_total, time_self, node.name)
    atomic_write('header.full.txt', content)

    # common header
    common = set()
    common_min = args.common_pct * len(sources) / 100
    for node in anytree.LevelOrderIter(htree):
        if node.header.ok() \
           and node.header.count >= common_min \
           and node.header.time['cpu'] >= args.min_duration \
           and not filter_up(node, lambda x: x.name in common):
            common.add(node.name)
    write_header_csv('common', headers, common)

    # top headers
    write_header_csv('top', headers, [x for x in headers if headers[x].ok()])

main()
