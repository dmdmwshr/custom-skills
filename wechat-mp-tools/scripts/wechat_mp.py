#!/usr/bin/env python3
"""Bounded observations of the existing local app; never schedules or submits jobs."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.dont_write_bytecode = True
INSTALLATION = Path('D:/Program_Files/wechatAutoRSSTool/INSTALLATION.json')
POOL_KEYS = ('total', 'active', 'cooldown', 'invalid', 'banned')
SETTINGS_KEYS = ('download_dir', 'request_delay', 'max_retries', 'rss_upload_enabled',
                 'xhs_auto_collect_enabled', 'channels_auto_harvest_enabled')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, base='http://127.0.0.1:5200', timeout=30):
        parts = urlsplit(base)
        if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in ('', '/')):
            raise ValueError('Only a verified loopback HTTP origin is supported')
        self.base, self.timeout = base.rstrip('/'), timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def get(self, path):
        try:
            with self.opener.open(Request(self.base + path), timeout=self.timeout) as response:
                payload = response.read(8 * 1024 * 1024 + 1)
                if len(payload) > 8 * 1024 * 1024:
                    raise ValueError('Response too large; inspect a smaller observation')
                return json.loads(payload)
        except HTTPError as exc:
            raise RuntimeError(f'Local API HTTP {exc.code}; no retry performed') from None
        except (URLError, TimeoutError):
            raise RuntimeError('Local API unavailable or timed out; no retry performed') from None


def sha256(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def auth_summary(data):
    pool = data.get('pool_summary', {})
    return {'logged_in': data.get('logged_in') is True,
            'login_status': data.get('login_state', {}).get('status'),
            'pool': {key: pool.get(key) for key in POOL_KEYS}}


def healthy(summary):
    pool = summary['pool']
    return (summary['logged_in'] and isinstance(pool.get('active'), int)
            and pool['active'] > 0 and pool.get('cooldown') == 0 and pool.get('banned') == 0)


def doctor(api, installation):
    record = json.loads(installation.read_text(encoding='utf-8-sig'))
    exe = Path(record['exe'])
    settings = api.get('/api/settings')
    accounts = api.get('/api/accounts').get('accounts', [])
    subscriptions = api.get('/api/accounts/rss-subscriptions').get('subscriptions', [])
    actual_hash = sha256(exe) if exe.is_file() else None
    return {'installation': {'version': record.get('version'), 'exe': str(exe),
                            'exe_exists': exe.is_file(), 'exe_sha256': actual_hash,
                            'recorded_hash_matches': bool(actual_hash) and actual_hash == record.get('exe_sha256'),
                            'data_dir': record.get('data_dir')},
            'auth': auth_summary(api.get('/api/auth/status')),
            'settings': {key: settings.get(key) for key in SETTINGS_KEYS},
            'accounts': [{key: row.get(key) for key in ('fakeid', 'nickname', 'bookId')}
                         for row in accounts],
            'subscriptions': [{key: row.get(key) for key in ('fakeid', 'nickname', 'enabled')}
                              for row in subscriptions],
            'process_binding_verified': False, 'remote_articles_requested': False}


def page(api, fakeid, begin, count):
    before = auth_summary(api.get('/api/auth/status'))
    result = {'fakeid': fakeid, 'begin': begin, 'requested_count': count,
              'before': before, 'listing_complete': False}
    if not healthy(before):
        return {**result, 'state': 'blocked_before_request', 'upstream_requested': False}, 2
    result['upstream_requested'] = True
    try:
        raw = api.get('/api/articles/list/' + quote(fakeid, safe='') + '?' +
                      urlencode({'begin': begin, 'count': count}))
        if not isinstance(raw, dict) or not isinstance(raw.get('articles'), list):
            raise ValueError('Unexpected list response schema')
        result['response'] = raw
    except (RuntimeError, ValueError) as exc:
        result.update(state='request_unknown', error=str(exc))
    try:
        result['after'] = auth_summary(api.get('/api/auth/status'))
    except (RuntimeError, ValueError) as exc:
        result.update(state='postcheck_unknown', postcheck_error=str(exc))
        return result, 2
    if 'response' not in result:
        return result, 2
    if raw.get('error') or not healthy(result['after']):
        result['state'] = 'blocked_or_error'
    elif not raw['articles']:
        result['state'] = 'empty_unconfirmed'
    else:
        result['state'] = 'page_received'
    return result, 0 if result['state'] == 'page_received' else 2


def task(api, task_id):
    raw = api.get('/api/articles/download-status/' + quote(task_id, safe=''))
    results = raw.get('results', [])
    terminal = raw.get('status') in ('completed', 'failed', 'cancelled')
    counts_match = (raw.get('completed') == sum(x.get('success') is True for x in results)
                    and raw.get('failed') == sum(x.get('success') is False for x in results))
    return {'task_id': task_id, 'terminal': terminal, 'response': raw,
            'counts_match_results': counts_match,
            'all_requested_downloaded': (counts_match and raw.get('status') == 'completed'
                                         and raw.get('failed') == 0
                                         and raw.get('completed') == raw.get('total')
                                         and len(results) == raw.get('total')),
            'files_verified': False, 'listing_complete': False}


class ImageRefs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs, self.srcsets = [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag.lower() in ('img', 'source') and values.get('srcset'):
            self.srcsets.append(values['srcset'])
        if tag.lower() == 'img':
            # Rendered src takes precedence over the original lazy-load URL.
            ref = values.get('src') or values.get('data-src')
            if ref:
                self.refs.append(ref)


def link_path(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def original_html(path):
    """Identify the app's retained source only with its matching generated file and metadata."""
    metadata = path.parent / 'metadata.json'
    if not path.name.endswith('_raw.html') or not metadata.is_file() or link_path(metadata):
        return False
    try:
        if metadata.stat().st_size > 1024 * 1024:
            return False
        title = json.loads(metadata.read_text(encoding='utf-8-sig')).get('title')
        return (isinstance(title, str) and Path(title).name == title
                and path.name == title + '_raw.html' and (path.parent / (title + '.html')).is_file())
    except (OSError, ValueError):
        return False


def verify(root):
    if link_path(root):
        raise ValueError('Expected a physical directory, not a link or junction')
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Expected an article directory')
    report = {'root': str(root), 'files': [], 'html_count': 0, 'readable_html_count': 0, 'text_count': 0,
              'source_html_excluded_from_offline_check': [],
              'missing_images': [], 'remote_images': [], 'outside_references': [],
              'uninspected_links': [], 'uninspected_srcsets': [], 'body_quality_checked': False}
    for directory, dirs, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in list(dirs):
            if link_path(parent / name):
                dirs.remove(name)
                report['uninspected_links'].append((parent / name).relative_to(root).as_posix())
        for name in sorted(names):
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if link_path(path) or not path.resolve().is_relative_to(root):
                report['uninspected_links'].append(relative)
                continue
            report['files'].append({'path': relative, 'bytes': path.stat().st_size, 'sha256': sha256(path)})
            if path.suffix.lower() == '.txt':
                report['text_count'] += 1
            if path.suffix.lower() not in ('.html', '.htm'):
                continue
            report['html_count'] += 1
            if original_html(path):
                report['source_html_excluded_from_offline_check'].append(relative)
                continue
            report['readable_html_count'] += 1
            parser = ImageRefs()
            parser.feed(path.read_text(encoding='utf-8-sig'))
            report['uninspected_srcsets'].extend({'html': relative, 'srcset': ref} for ref in parser.srcsets)
            for ref in parser.refs:
                entry = {'html': relative, 'reference': ref}
                url = urlsplit(ref)
                if url.scheme == 'data':
                    continue
                if url.scheme in ('http', 'https') or url.netloc:
                    report['remote_images'].append(entry)
                    continue
                target = (path.parent / unquote(url.path)).resolve()
                if url.scheme or not target.is_relative_to(root):
                    report['outside_references'].append(entry)
                elif not target.is_file() or target.stat().st_size == 0:
                    report['missing_images'].append(entry)
    report['local_image_references_ok'] = not any(report[key] for key in
        ('missing_images', 'remote_images', 'outside_references', 'uninspected_links', 'uninspected_srcsets'))
    report['all_articles_complete'] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:5200')
    parser.add_argument('--timeout', type=float, default=30)
    commands = parser.add_subparsers(dest='command', required=True)
    doc = commands.add_parser('doctor')
    doc.add_argument('--installation', type=Path, default=INSTALLATION)
    pg = commands.add_parser('page')
    pg.add_argument('--fakeid', required=True)
    pg.add_argument('--begin', type=int, required=True)
    pg.add_argument('--count', type=int, default=5)
    ts = commands.add_parser('task')
    ts.add_argument('--task-id', required=True)
    vf = commands.add_parser('verify')
    vf.add_argument('--root', type=Path, required=True)
    for sub in (doc, pg, ts, vf):
        sub.add_argument('--out', type=Path, required=sub is pg)
    args = parser.parse_args()
    if args.command == 'page' and (args.begin < 0 or not 1 <= args.count <= 20):
        parser.error('begin must be >=0; count must be 1..20 for this verified interface')
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    if args.command == 'verify' and args.out and args.out.resolve().is_relative_to(args.root.resolve()):
        parser.error('Save the report outside the directory being verified')
    output = None
    try:
        if args.out:
            # Reserve before a page request; interruption leaves explicit evidence.
            output = args.out.open('x', encoding='utf-8')
            output.write('{"state":"observation_in_progress_or_unknown"}\n')
            output.flush()
        api = API(args.base_url, args.timeout)
        exit_code = 0
        if args.command == 'doctor':
            result = doctor(api, args.installation)
        elif args.command == 'page':
            result, exit_code = page(api, args.fakeid, args.begin, args.count)
        elif args.command == 'task':
            result = task(api, args.task_id)
        else:
            result = verify(args.root)
            exit_code = 0 if result['local_image_references_ok'] else 2
        if output:
            output.seek(0)
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.truncate()
            print(json.dumps({'report': str(args.out.resolve()), 'command': args.command,
                              'state': result.get('state', 'observed'), 'exit_code': exit_code}, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'error': str(exc), 'retry_performed': False}, ensure_ascii=False))
        return 2
    finally:
        if output:
            output.close()


if __name__ == '__main__':
    raise SystemExit(main())
