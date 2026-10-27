#!/usr/bin/env python3
"""Windows WPS 内置 CLI 的受限调用器；仅 catalog/probe/run，标准库。"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import ntpath
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import urllib.parse
import uuid
import zipfile

CLI_VERSION = "1.1.0"
CATALOG = {'word2pdf': {'options': {'range': 'range',
                          'pdf-options': 'opt[,opt...]',
                          'image-quality': 'low|medium|high',
                          'timeout': 'seconds'},
              'helpSha256': 'ceabcbcbd21b78ebc84b518c1d9218349c884fa31c42b504b8dd5f83cc41405c',
              'exitCodes': {'0': 'Success.',
                            '3': 'Timeout.',
                            '100': 'Not signed in.',
                            '101': 'Account lacks the VIP privilege.',
                            '201': 'Missing required argument.',
                            '203': 'Input file not found.',
                            '207': 'Input file exceeds 200 MB.',
                            '209': 'Source requires a password.',
                            '210': 'Supplied password is incorrect.',
                            '211': 'Output parent directory does not exist.',
                            '212': 'Permission denied: no write access to output directory.'}},
 'excel2pdf': {'options': {'range': 'range',
                           'pdf-options': 'opt[,opt...]',
                           'image-quality': 'low|medium|high',
                           'timeout': 'seconds'},
               'helpSha256': '7d74477f7c8feddcbab0bf6695d25c4275fd2391b1bd38139115523fa6b177e4',
               'exitCodes': {'0': 'Success.',
                             '3': 'Timeout.',
                             '100': 'Not signed in.',
                             '101': 'Account lacks the VIP privilege.',
                             '201': 'Missing required argument.',
                             '203': 'Input file not found.',
                             '207': 'Input file exceeds 200 MB.',
                             '209': 'Source requires a password.',
                             '210': 'Supplied password is incorrect.',
                             '211': 'Output parent directory does not exist.',
                             '212': 'Permission denied: no write access to output directory.'}},
 'ppt2pdf': {'options': {'range': 'range',
                         'output-type': 'type',
                         'handout-layout': 'n',
                         'handout-order': 'order',
                         'image-quality': 'low|medium|high',
                         'timeout': 'seconds'},
             'helpSha256': 'd96f0ae649f51f1c965a308b8fe5b3726e5bd1d29ddc22a31adfc604d750dfaa',
             'exitCodes': {'0': 'Success.',
                           '3': 'Timeout.',
                           '100': 'Not signed in.',
                           '101': 'Account lacks the VIP privilege.',
                           '201': 'Missing required argument.',
                           '203': 'Input file not found.',
                           '207': 'Input file exceeds 200 MB.',
                           '209': 'Source requires a password.',
                           '210': 'Supplied password is incorrect.',
                           '211': 'Output parent directory does not exist.',
                           '212': 'Permission denied: no write access to output directory.'}},
 'pdf2word': {'options': {'suffix': 'docx|doc|rtf',
                          'range': 'range',
                          'image-quality': 'low|medium|high|best',
                          'engine': 'auto|ai|basic',
                          'scanned': 'true|false',
                          'ai-fix': 'true|false',
                          'timeout': 'seconds'},
              'helpSha256': '25baea3216cdf6b2b3c31a64df6ee20b45ff0e339b1a91ccd1b73dcdbfeff1aa',
              'exitCodes': {'0': 'Success.',
                            '1': 'Generic failure (business error).',
                            '3': 'Timeout reached.',
                            '100': 'Not signed in.',
                            '101': 'Account lacks the VIP privilege.',
                            '201': 'Missing required argument.',
                            '202': 'Unknown argument or value.',
                            '203': 'Input file not found.',
                            '207': 'Input file exceeds 200 MB.',
                            '209': 'Source PDF requires a password.',
                            '210': 'Supplied password is incorrect.',
                            '211': 'Output parent directory does not exist.',
                            '212': 'Permission denied: no write access to output directory.',
                            '220': 'Invalid --range value or out-of-range pages.'}},
 'pdf2excel': {'options': {'suffix': 'xlsx|xls|et|csv',
                           'range': 'range',
                           'image-quality': 'low|medium|high|best',
                           'engine': 'auto|ai|basic',
                           'scanned': 'true|false',
                           'ai-fix': 'true|false',
                           'multi-sheet': 'true|false',
                           'timeout': 'seconds'},
               'helpSha256': '9d47900a6b0b99919429ea00b2bbe6efa1746ce29060d066d2d975cb3d6bb06a',
               'exitCodes': {'0': 'Success.',
                             '3': 'Timeout.',
                             '100': 'Not signed in.',
                             '101': 'Account lacks the VIP privilege.',
                             '201': 'Missing required argument.',
                             '202': 'Unknown argument or value.',
                             '207': 'Input file exceeds 200 MB.',
                             '209': 'Source PDF requires a password.',
                             '210': 'Supplied password is incorrect.',
                             '211': 'Output parent directory does not exist.',
                             '212': 'Permission denied: no write access to output directory.',
                             '220': 'Invalid --range value.'}},
 'pdf2ppt': {'options': {'suffix': 'pptx|ppt|dps',
                         'range': 'range',
                         'image-quality': 'low|medium|high|best',
                         'engine': 'auto|ai|basic',
                         'scanned': 'true|false',
                         'ai-fix': 'true|false',
                         'timeout': 'seconds'},
             'helpSha256': 'efd30a8d30f0c4d67480fb7c79d22109f8ede445c6a1141b9e3319a3700f920b',
             'exitCodes': {'0': 'Success.',
                           '3': 'Timeout.',
                           '100': 'Not signed in.',
                           '101': 'Account lacks the VIP privilege.',
                           '201': 'Missing required argument.',
                           '207': 'Input file exceeds 200 MB.',
                           '209': 'Source PDF requires a password.',
                           '210': 'Supplied password is incorrect.',
                           '211': 'Output parent directory does not exist.',
                           '212': 'Permission denied: no write access to output directory.',
                           '220': 'Invalid --range value.'}},
 'pdf2txt': {'options': {'range': 'range', 'timeout': 'seconds'},
             'helpSha256': '461c95d0ef5a13c2970cf127b1e39c953ea09f3a2ac343b5a97785b96eb24bc0',
             'exitCodes': {'0': 'Success.',
                           '3': 'Timeout.',
                           '100': 'Not signed in.',
                           '101': 'Account lacks the VIP privilege.',
                           '201': 'Missing required argument.',
                           '207': 'Input file exceeds 200 MB.',
                           '209': 'Source PDF requires a password.',
                           '210': 'Supplied password is incorrect.',
                           '211': 'Output parent directory does not exist.',
                           '212': 'Permission denied: no write access to output directory.',
                           '220': 'Invalid --range value.'}},
 'pdf2md': {'options': {'range': 'range',
                        'image-quality': 'low|medium|high|best',
                        'engine': 'auto|ai|basic',
                        'timeout': 'seconds'},
            'helpSha256': 'ffffa368ea09d8405efbd5b133c6fbb8b4047ccf959a8b897e3b84d53e77faf0',
            'exitCodes': {'0': 'Success.',
                          '3': 'Timeout.',
                          '100': 'Not signed in.',
                          '101': 'Account lacks the VIP privilege.',
                          '201': 'Missing required argument.',
                          '207': 'Input file exceeds 200 MB.',
                          '209': 'Source PDF requires a password.',
                          '210': 'Supplied password is incorrect.',
                          '211': 'Output parent directory does not exist.',
                          '212': 'Permission denied: no write access to output directory.',
                          '220': 'Invalid --range value.'}},
 'pdf2photo': {'options': {'suffix': 'jpg|jpeg|png',
                           'range': 'range',
                           'image-quality': 'low|medium|high|best',
                           'color': 'color|grayscale|blackwhite',
                           'timeout': 'seconds'},
               'helpSha256': 'a72749e0933034262ae10e445bbb27f2b049d96f4992725209d39cd1eed38792',
               'exitCodes': {'0': 'Success.',
                             '3': 'Timeout.',
                             '100': 'Not signed in.',
                             '101': 'Account lacks the VIP privilege.',
                             '201': 'Missing required argument.',
                             '202': 'Unknown argument or value.',
                             '209': 'Source PDF requires a password.',
                             '210': 'Supplied password is incorrect.',
                             '211': 'Output directory does not exist.',
                             '212': 'Permission denied: no write access to output directory.'}},
 'pdf2imgpdf': {'options': {'range': 'range', 'color': 'color|blackwhite', 'timeout': 'seconds'},
                'helpSha256': '9b3335513dd6a7931b53651c2c6369772e89956d6a7e5c17cd75e315ec56d643',
                'exitCodes': {'0': 'Success.',
                              '3': 'Timeout.',
                              '100': 'Not signed in.',
                              '101': 'Account lacks the VIP privilege.',
                              '201': 'Missing required argument.',
                              '202': 'Unknown argument or value.',
                              '207': 'Input file exceeds 200 MB.',
                              '209': 'Source PDF requires a password.',
                              '210': 'Supplied password is incorrect.',
                              '211': 'Output parent directory does not exist.',
                              '212': 'Permission denied: no write access to output directory.'}},
 'pdfcompress': {'options': {'press-quality': 'low|medium|high|best',
                             'color-img-quality': '0-100',
                             'color-img-dest-ppi': '9-2400',
                             'color-img-threshold-ppi': '9-2400',
                             'gray-img-dest-ppi': '9-2400',
                             'gray-img-threshold-ppi': '9-2400',
                             'mono-img-dest-ppi': '9-2400',
                             'mono-img-threshold-ppi': '9-2400',
                             'optimizer-settings': 'opt[,opt...]',
                             'timeout': 'seconds'},
                 'helpSha256': 'f8a478ff963549cd3b843d79a1c7f10805c6aec12815cfb173b9e66cfce9b1ca',
                 'exitCodes': {'0': 'Success.',
                               '3': 'Timeout.',
                               '100': 'Not signed in.',
                               '101': 'Account lacks the VIP privilege.',
                               '201': 'Missing required argument.',
                               '209': 'Source PDF requires a password.',
                               '210': 'Supplied password is incorrect.',
                               '211': 'Output parent directory does not exist.',
                               '212': 'Permission denied: no write access to output directory.'}},
 'pdfsplit': {'options': {'range': 'ranges', 'timeout': 'seconds'},
              'helpSha256': 'e4357addd12aa06eef5eaa4fb22ee8899fd7d113176b0a17e8a55248fb1fa76d',
              'exitCodes': {'0': 'Success.',
                            '3': 'Timeout.',
                            '100': 'Not signed in.',
                            '101': 'Account lacks the VIP privilege.',
                            '201': 'Missing required argument (e.g. --range).',
                            '209': 'Source PDF requires a password.',
                            '210': 'Supplied password is incorrect.',
                            '211': 'Output directory does not exist.',
                            '212': 'Permission denied: no write access to output directory.',
                            '220': 'Invalid --range value.'}},
 'pdfmerge': {'options': {'range': 'ranges', 'add-odd-page': 'true|false', 'timeout': 'seconds'},
              'helpSha256': '5a37de3734a4d24585b3a88cd8776c5d7036f28b911f7f6d32f4a46d9a229557',
              'exitCodes': {'0': 'Success.',
                            '3': 'Timeout.',
                            '100': 'Not signed in.',
                            '101': 'Account lacks the VIP privilege.',
                            '201': 'Missing required argument (e.g. --range/--password count does not match '
                                   'inputs).',
                            '209': 'Source PDF requires a password.',
                            '210': 'Supplied password is incorrect.'}},
 'pdfwatermark': {'options': {'text': 'text',
                              'image': 'path',
                              'range': 'range',
                              'opacity': '0-100',
                              'rotate-angle': 'deg',
                              'font-size': 'pt',
                              'text-color': '#RRGGBB',
                              'lines': 'n',
                              'font-name': 'name',
                              'font-bold': 'true|false',
                              'scale': 'factor',
                              'h-align': 'left|center|right',
                              'v-align': 'top|center|bottom',
                              'timeout': 'seconds'},
                  'helpSha256': '28996f1c08089e706b289e5ed0269e30cab1e0dbd636eff27fc20691931f0904',
                  'exitCodes': {'0': 'Success.',
                                '3': 'Timeout.',
                                '100': 'Not signed in.',
                                '101': 'Account lacks the VIP privilege.',
                                '201': 'Missing required argument (--text or --image).',
                                '202': 'Conflicting --text and --image.',
                                '209': 'Source PDF requires a password.',
                                '210': 'Supplied password is incorrect.',
                                '220': 'Invalid --range value or out-of-range pages.'}},
 'pdfremovewatermark': {'options': {'fail-on-signature': 'true|false',
                                    'fail-on-no-watermark': 'true|false',
                                    'timeout': 'seconds'},
                        'helpSha256': 'f8136fbde297c5a43477bbd014004db3f0c7a3bc9b17896fc970cb9c904d7dbb',
                        'exitCodes': {'0': 'Success.',
                                      '1': 'Watermark not detected (with --fail-on-no-watermark true).',
                                      '3': 'Timeout.',
                                      '100': 'Not signed in.',
                                      '101': 'Account lacks the VIP privilege.',
                                      '201': 'Missing required argument.',
                                      '209': 'Source PDF requires a password.',
                                      '210': 'Supplied password is incorrect.'}},
 'pdfencrypt': {'options': {'allow-modify': 'true|false',
                            'allow-copy': 'true|false',
                            'allow-annotation': 'true|false',
                            'print-permission': 'full|degraded|none',
                            'timeout': 'seconds'},
                'helpSha256': '5bd85ac735104da91159d748ae9dc8e51b27f7822578ac99b83210a555788c1b',
                'exitCodes': {'0': 'Success.',
                              '3': 'Timeout.',
                              '100': 'Not signed in.',
                              '201': 'Missing required argument (--open-pwd or --edit-pwd).',
                              '207': 'Input file exceeds 200 MB.',
                              '209': 'Source PDF requires a password.',
                              '210': 'Supplied password is incorrect.',
                              '211': 'Output parent directory does not exist.',
                              '212': 'Permission denied: no write access to output directory.'}},
 'pdfinfo': {'options': {},
             'helpSha256': 'a13b93259fca68cf9903e2c11f6b0ad909e64909b745267c980954df877ac6b7',
             'exitCodes': {'0': 'Success.',
                           '5': 'PDF engine failed to initialize (dependency missing).',
                           '100': 'Not signed in.',
                           '201': 'Missing argument (e.g. password count mismatch in batch).',
                           '202': 'Unknown argument.',
                           '203': 'Input path missing, not found, or no glob matches.',
                           '204': 'Failed to open the PDF (corrupt or unsupported).',
                           '209': 'Source PDF requires a password.',
                           '210': 'Supplied password is incorrect.',
                           '218': 'Input suffix not supported for this command.'}},
 'word2photo': {'options': {'suffix': 'png|jpg|jpeg|bmp|tif',
                            'range': 'range',
                            'long-image': 'true|false',
                            'dpi': 'number',
                            'timeout': 'seconds'},
                'helpSha256': 'c8ee36128ef41972834bd08523bdb81ce9ae450ace99d788c72c9be096d0c256',
                'exitCodes': {'0': 'Success.',
                              '1': 'Conversion failed.',
                              '3': 'Timeout.',
                              '100': 'Not signed in.',
                              '101': 'Account lacks the VIP privilege.',
                              '201': 'Missing required argument.',
                              '202': 'Unknown argument or value.',
                              '203': 'Input file not found.',
                              '211': 'Output directory does not exist.',
                              '212': 'Permission denied: no write access to output directory.',
                              '216': 'Output path too long.',
                              '218': 'Input or output format not supported.'}},
 'excel2photo': {'options': {'suffix': 'png|jpg|jpeg|bmp|tif',
                             'range': 'range',
                             'long-image': 'true|false',
                             'dpi': 'number',
                             'timeout': 'seconds'},
                 'helpSha256': 'ab09a6bc19e36510102bfa63afdeb4a056ea07b790ac4425eb25be8987f314dd',
                 'exitCodes': {'0': 'Success.',
                               '1': 'Conversion failed.',
                               '3': 'Timeout.',
                               '100': 'Not signed in.',
                               '101': 'Account lacks the VIP privilege.',
                               '201': 'Missing required argument.',
                               '202': 'Unknown argument or value.',
                               '203': 'Input file not found.',
                               '211': 'Output directory does not exist.',
                               '212': 'Permission denied: no write access to output directory.',
                               '216': 'Output path too long.',
                               '218': 'Input or output format not supported.'}},
 'ppt2photo': {'options': {'suffix': 'png|jpg|jpeg|bmp|tif',
                           'range': 'range',
                           'long-image': 'true|false',
                           'dpi': 'number',
                           'timeout': 'seconds'},
               'helpSha256': '688d0f421544f3775387937d6c31e50ccff0cf77a5afa759d1257dea15131786',
               'exitCodes': {'0': 'Success.',
                             '1': 'Conversion failed.',
                             '3': 'Timeout.',
                             '100': 'Not signed in.',
                             '101': 'Account lacks the VIP privilege.',
                             '201': 'Missing required argument.',
                             '202': 'Unknown argument or value.',
                             '203': 'Input file not found.',
                             '211': 'Output directory does not exist.',
                             '212': 'Permission denied: no write access to output directory.',
                             '216': 'Output path too long.',
                             '218': 'Input or output format not supported.'}},
 'photo2pdf': {'options': {'merge': 'true|false',
                           'page-width': 'mm',
                           'page-height': 'mm',
                           'page-margin': 'mm',
                           'force-cover': 'true|false',
                           'overwrite': 'true|false',
                           'timeout': 'seconds'},
               'helpSha256': '6f1e587fd7ecc21c2123cab15919e6e95aa2ab44df66c59ad955a5b47d8c6597',
               'exitCodes': {'0': 'Success.',
                             '3': 'Timeout.',
                             '100': 'Not signed in.',
                             '101': 'Account lacks the VIP privilege.',
                             '201': 'Missing required argument.',
                             '211': 'Output directory does not exist.',
                             '212': 'Permission denied: no write access to output directory.',
                             '214': 'Output save error.'}},
 'photo2word': {'options': {'suffix': 'docx|doc|wps|rtf', 'merge': 'true|false', 'timeout': 'seconds'},
                'helpSha256': 'f6dcd8fe04c413da484fad04944fc194be57e0768eeed0f04306ef3afcb9530a',
                'exitCodes': {'0': 'Success.',
                              '1': 'Conversion failed.',
                              '3': 'Timeout.',
                              '100': 'Not signed in.',
                              '101': 'Account lacks the VIP privilege.',
                              '201': 'Missing required argument.',
                              '214': 'Output save error.'}},
 'photo2excel': {'options': {'suffix': 'xlsx|xls|et', 'merge': 'true|false', 'timeout': 'seconds'},
                 'helpSha256': '74dfb3c41e690c247753842e1be30a2a041ea490f910b7d1a694c1c2fe894943',
                 'exitCodes': {'0': 'Success.',
                               '1': 'Conversion failed.',
                               '3': 'Timeout.',
                               '100': 'Not signed in.',
                               '101': 'Account lacks the VIP privilege.',
                               '201': 'Missing required argument.',
                               '214': 'Output save error.'}},
 'photo2ppt': {'options': {'suffix': 'pptx|ppt|dps', 'merge': 'true|false', 'timeout': 'seconds'},
               'helpSha256': '7815be86cd125bc358d8c62e33459fdc0f1ef948975e14b49f86050f12979101',
               'exitCodes': {'0': 'Success.',
                             '1': 'Conversion failed.',
                             '3': 'Timeout.',
                             '100': 'Not signed in.',
                             '101': 'Account lacks the VIP privilege.',
                             '201': 'Missing required argument.',
                             '214': 'Output save error.'}},
 'txt2pdf': {'options': {'page-width': 'mm', 'page-height': 'mm', 'timeout': 'seconds'},
             'helpSha256': '783e45d4f483eb8d92e25ba3267534f1d7bc8514f1603f5c0b83ca8c6b2dbd02',
             'exitCodes': {'0': 'Success.',
                           '3': 'Timeout.',
                           '100': 'Not signed in.',
                           '101': 'Account lacks the VIP privilege.',
                           '201': 'Missing required argument.',
                           '211': 'Output parent directory does not exist.',
                           '212': 'Permission denied: no write access to output directory.',
                           '214': 'Output save error.'}},
 'pdf2cad': {'options': {'range': 'range',
                         'suffix': 'dwg|dwt|dxf',
                         'multi-sheet': 'true|false',
                         'timeout': 'seconds'},
             'helpSha256': 'f27a15ac84aad1691f514c2af3c4d899f502846a46c20ae09a4db43e834b1aaf',
             'exitCodes': {'0': 'Success.',
                           '3': 'Timeout.',
                           '100': 'Not signed in.',
                           '101': 'Account lacks the VIP privilege.',
                           '201': 'Missing required argument (e.g. --range).',
                           '202': 'Unknown argument or value.',
                           '209': 'Source PDF requires a password.',
                           '210': 'Supplied password is incorrect.',
                           '220': 'Invalid --range value.'}},
 'cad2pdf': {'options': {'color': 'color|blackwhite',
                         'page-width': 'mm',
                         'page-height': 'mm',
                         'multi-sheet': 'true|false',
                         'timeout': 'seconds'},
             'helpSha256': '65982a6fc706d8e7000f3b7c00793d495d16a090da044166e1b479e38af36810',
             'exitCodes': {'0': 'Success.',
                           '3': 'Timeout.',
                           '100': 'Not signed in.',
                           '101': 'Account lacks the VIP privilege.',
                           '201': 'Missing required argument.',
                           '202': 'Unknown argument or value.',
                           '211': 'Output parent directory does not exist.',
                           '212': 'Permission denied: no write access to output directory.',
                           '222': 'Invalid page size.'}},
 'caj2pdf': {'options': {'timeout': 'seconds'},
             'helpSha256': 'a7745cd7963e20e7dae59760758a49d628110cd0ffcb2aef1ed0e614a5b5f15e',
             'exitCodes': {'0': 'Success.',
                           '3': 'Timeout.',
                           '100': 'Not signed in.',
                           '101': 'Account lacks the VIP privilege.',
                           '201': 'Missing required argument.',
                           '203': 'Input file not found.',
                           '211': 'Output parent directory does not exist.',
                           '212': 'Permission denied: no write access to output directory.',
                           '214': 'Output save error.'}}}

SECRET_KEYS = {'password', 'openPassword', 'editPassword', 'open-pwd', 'edit-pwd'}
SCANNERS = {'pdf2word', 'pdf2excel', 'pdf2ppt'}
PHOTOS = {'photo2word', 'photo2excel', 'photo2ppt'}
OFFICE_PHOTOS = {'word2photo', 'excel2photo', 'ppt2photo'}
MULTI_RANGES = {'excel2pdf', 'ppt2pdf', 'word2photo', 'excel2photo', 'ppt2photo', 'pdfsplit'}
EXTENSIONS = {
    'word': {'.doc', '.docx', '.wps', '.rtf'},
    'excel': {'.xls', '.xlsx', '.et', '.csv'},
    'ppt': {'.ppt', '.pptx', '.dps'},
    'photo': {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'},
    'cad': {'.dwg', '.dwt', '.dxf'}, 'txt': {'.txt'}, 'caj': {'.caj'}, 'pdf': {'.pdf'}
}
DEFAULT_SUFFIX = {'pdf2word': 'docx', 'pdf2excel': 'xlsx', 'pdf2ppt': 'pptx',
                  'pdf2txt': 'txt', 'pdf2md': 'md', 'pdf2cad': 'zip',
                  'photo2word': 'docx', 'photo2excel': 'xlsx', 'photo2ppt': 'pptx'}
TOP_KEYS = {'schemaVersion', 'runId', 'command', 'inputs', 'output', 'outputKind',
            'outputScope', 'options', 'password', 'openPassword', 'editPassword',
            'cloudProcessingAuthorized', 'cloudAuthorizationBasis', 'authorizedInputHashes',
            'evidenceDir', 'wpscliPath', 'parentTimeoutSeconds'}


class Rejected(ValueError):
    """已知的启动前拒绝；不回显用户值。"""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def secret_values(value):
    found = []
    def visit(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in SECRET_KEYS:
                    for item in (v if isinstance(v, list) else [v]):
                        if isinstance(item, str) and item:
                            found.append(item)
                visit(v)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)
    visit(value)
    return sorted(set(found), key=len, reverse=True)


def redact(value, secrets):
    if isinstance(value, str):
        for s in secrets:
            # Also cover the native process echoing an escaped JSON string.
            for variant in {s, json.dumps(s, ensure_ascii=True)[1:-1],
                            json.dumps(s, ensure_ascii=False)[1:-1]}:
                value = value.replace(variant, '[REDACTED]')
        return value
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, dict):
        return {redact(str(k), secrets): redact(v, secrets) for k, v in value.items()}
    return value


def durable_json(path, value, secrets=(), exclusive=False):
    """只写本工具自建记录；排他 intent 落盘后才能启动任何进程。"""
    path = Path(path)
    data = json_bytes(redact(value, secrets))
    if exclusive:
        with path.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    else:
        pending = path.with_name(path.name + '.pending-' + uuid.uuid4().hex)
        with pending.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(pending, path)
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def safe_text(v):
    require(isinstance(v, str) and bool(v) and not v.startswith('-'), 'invalid_string_value')
    require(not any(ord(c) < 32 for c in v) and '\ufffd' not in v, 'invalid_text_encoding_or_control')
    return v


def windows_syntax(value):
    value = safe_text(value)
    require(re.match(r'^[A-Za-z]:[\\/]', value) is not None, 'drive_absolute_path_required')
    require(not any(c in value[2:] for c in ':*?"<>|'), 'unsafe_windows_path')
    parts = re.split(r'[\\/]', value[3:])
    for i, part in enumerate(parts):
        if not part and i == len(parts) - 1:
            continue
        require(bool(part) and part not in {'.', '..'} and not part.endswith(('.', ' ')), 'ambiguous_path_component')
        require(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', part.split('.')[0], re.I) is None,
                'reserved_windows_path')
    return ntpath.normpath(value)


def winpath(value):
    """生产路径解析；测试可用 unittest.mock 替换函数，JSON 无旁路开关。"""
    return Path(windows_syntax(value)).resolve()


def under(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def overlaps(a, b):
    return under(a, b) or under(b, a)


def stable_directory(path, identity=None):
    path = Path(path)
    require(path.is_dir() and not path.is_symlink() and
            not getattr(os.path, 'isjunction', lambda p: False)(path) and
            os.path.normcase(str(path.resolve())) == os.path.normcase(str(path)), 'directory_identity_changed')
    st = path.stat()
    actual = (st.st_dev, st.st_ino)
    require(identity is None or actual == identity, 'directory_identity_changed')
    return actual


def tree_paths(path, scope):
    """先核范围再遍历；不沿目录联接或符号链接读取邻居。"""
    for child in sorted(Path(path).iterdir()):
        require(not child.is_symlink() and
                not getattr(os.path, 'isjunction', lambda p: False)(child) and under(child, scope), 'artifact_escape')
        yield child
        if child.is_dir():
            yield from tree_paths(child, scope)


def require_windows():
    require(sys.platform == 'win32', 'windows_runtime_required')


def state_root():
    root = os.environ.get('LOCALAPPDATA')
    require(bool(root), 'localappdata_required')
    return winpath(str(Path(root) / 'dmdmwshr' / 'wps-office' / 'runs'))


def number(value, low=None, high=None, integer=False):
    require(type(value) in ((int,) if integer else (int, float)), 'numeric_type_required')
    require(math.isfinite(value), 'finite_number_required')
    require(low is None or value >= low, 'numeric_value_too_small')
    require(high is None or value <= high, 'numeric_value_too_large')


def validate_ranges(value, command, count):
    require(isinstance(value, str), 'range_string_required')
    segments = value.split(';')
    require(len(segments) == count, 'range_input_count_mismatch')
    allow_commas = command in MULTI_RANGES and command != 'pdfmerge'
    for segment in segments:
        spans = segment.split(',') if allow_commas else [segment]
        for span in spans:
            require(re.fullmatch(r'[1-9][0-9]*(?:-[1-9][0-9]*)?', span) is not None, 'invalid_range_syntax')
            ends = list(map(int, span.split('-')))
            require(len(ends) == 1 or ends[0] <= ends[1], 'reversed_range')


def output_shape(command, options, count):
    if command == 'pdfinfo':
        return 'none'
    merged = command == 'pdfmerge' or command in PHOTOS | {'photo2pdf'} and options.get('merge', command == 'photo2pdf')
    if merged:
        return 'file'
    if command in OFFICE_PHOTOS and options.get('long-image', False):
        require(count == 1, 'long_image_requires_single_input')
        return 'file'
    if command in {'pdfsplit', 'pdf2photo'} | OFFICE_PHOTOS or count > 1 or command == 'photo2pdf':
        return 'directory'
    return 'file'


def validate_request(request):
    require(isinstance(request, dict) and not set(request) - TOP_KEYS, 'unknown_request_field')
    require(type(request.get('schemaVersion')) is int and request['schemaVersion'] == 1, 'unsupported_schema')
    require(isinstance(request.get('runId'), str) and
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', request['runId']) is not None, 'invalid_run_id')
    command = request.get('command')
    require(isinstance(command, str) and command in CATALOG, 'command_not_allowed')
    inputs = request.get('inputs')
    require(isinstance(inputs, list) and 1 <= len(inputs) <= 128, 'input_array_required')
    for p in inputs:
        windows_syntax(p)
        require(';' not in p, 'native_input_delimiter_not_supported')
        family = 'pdf' if command.startswith('pdf') else next(k for k in EXTENSIONS if command.startswith(k))
        require(ntpath.splitext(p)[1].lower() in EXTENSIONS[family], 'unsupported_input_extension')
    require(len({ntpath.normcase(windows_syntax(p)) for p in inputs}) == len(inputs), 'duplicate_input')
    options = request.get('options', {})
    require(isinstance(options, dict), 'options_object_required')
    require(not set(options) - set(CATALOG[command]['options']), 'option_not_allowed_for_command')
    for k, v in options.items():
        kind = CATALOG[command]['options'][k]
        if kind == 'true|false':
            require(type(v) is bool, 'boolean_option_required')
        elif kind and '|' in kind:
            require(isinstance(v, str) and v in kind.split('|'), 'enum_value_not_allowed')
        elif k == 'range':
            validate_ranges(v, command, len(inputs))
        elif k == 'timeout':
            number(v, 1, 3600, integer=True)
        elif k == 'dpi':
            number(v, 72, 600, integer=True)
        elif k in {'opacity', 'color-img-quality'}:
            number(v, 0, 100, integer=True)
        elif k.endswith('-ppi'):
            number(v, 9, 2400, integer=True)
        elif k in {'page-width', 'page-height', 'font-size', 'scale'}:
            number(v, 0.001)
        elif k == 'page-margin':
            number(v, 0)
        elif k == 'rotate-angle':
            number(v, -360, 360)
        elif k == 'lines':
            number(v, 1, integer=True)
        elif k == 'handout-layout':
            require(type(v) is int and v in {1, 2, 3, 4, 6, 9}, 'invalid_handout_layout')
        elif k == 'handout-order':
            require(isinstance(v, str) and v in {'horizontal', 'vertical'}, 'invalid_handout_order')
        elif k == 'output-type':
            require(isinstance(v, str) and v in {'slides', 'handouts', 'notes-pages', 'outline'}, 'invalid_output_type')
        elif k == 'text-color':
            require(isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', v) is not None, 'invalid_text_color')
        elif k in {'pdf-options', 'optimizer-settings'}:
            allowed = {'summary-info', 'comments', 'hyperlinks'}
            if command == 'word2pdf':
                allowed |= {'footnotes-and-endnotes', 'title-to-bookmark', 'bookmark', 'other-to-bookmark', 'custom-to-bookmark'}
            if k == 'optimizer-settings':
                allowed = {'discard-form-action', 'flatten-form', 'discard-javascript', 'discard-print-settings',
                           'discard-search-index', 'subset-fonts', 'discard-bookmarks', 'discard-attachments',
                           'discard-app-data', 'flatten-layers'}
            require(isinstance(v, str) and bool(v) and set(v.split(',')) <= allowed, 'invalid_option_list')
        else:
            safe_text(v)
        if isinstance(v, str):
            safe_text(v)
    require(('page-width' in options) == ('page-height' in options), 'page_size_requires_pair')
    if command in SCANNERS:
        if options.get('scanned', False):
            require('range' in options, 'scanned_range_required')
            require(not {'engine', 'image-quality'} & options.keys(), 'scanned_engine_quality_conflict')
        require('ai-fix' not in options or options.get('scanned') is True, 'ai_fix_requires_scanned')
        require(not ('engine' in options and ({'scanned', 'ai-fix'} & options.keys())), 'engine_scanned_conflict')
    if command == 'pdf2excel':
        require(not (options.get('suffix') == 'csv' and options.get('multi-sheet', False)), 'csv_multi_sheet_conflict')
    if command in {'pdfsplit', 'pdf2cad'}:
        require('range' in options, 'range_required')
    if command == 'ppt2pdf':
        if {'handout-layout', 'handout-order'} & options.keys():
            require(options.get('output-type') == 'handouts', 'handouts_required')
        if 'handout-order' in options:
            require(options.get('handout-layout', 6) > 3, 'handout_order_requires_layout_gt3')
    if command == 'pdfwatermark':
        require(('text' in options) != ('image' in options), 'exactly_one_watermark_required')
        if 'image' in options:
            windows_syntax(options['image'])
    if command == 'photo2pdf':
        require(not options.get('overwrite', False), 'overwrite_not_supported')
    for key in ('password', 'openPassword', 'editPassword'):
        if key not in request:
            continue
        if key == 'password':
            require(command not in PHOTOS | {'photo2pdf', 'txt2pdf', 'cad2pdf', 'caj2pdf'}, 'password_not_supported')
        else:
            require(command == 'pdfencrypt', 'password_not_supported')
        values = request[key] if isinstance(request[key], list) else [request[key]]
        require(len(values) == (len(inputs) if key == 'password' else 1), 'password_input_count_mismatch')
        for value in values:
            safe_text(value)
            require(';' not in value, 'native_password_delimiter_not_supported')
        if len(inputs) > 1 and command in OFFICE_PHOTOS and key == 'password':
            raise Rejected('office_photo_batch_password_unsupported')
    if command == 'pdfencrypt':
        require('openPassword' in request or 'editPassword' in request, 'encryption_password_required')
    shape = output_shape(command, options, len(inputs))
    require(request.get('outputKind') == shape, 'output_kind_mismatch')
    if shape == 'none':
        require(not {'output', 'outputScope'} & request.keys(), 'pdfinfo_has_no_output')
    else:
        windows_syntax(request.get('output'))
        if 'outputScope' in request:
            windows_syntax(request['outputScope'])
        if shape == 'file':
            suffix = DEFAULT_SUFFIX.get(command, 'pdf')
            if command in OFFICE_PHOTOS:
                suffix = options.get('suffix', 'png')
            elif command in {'pdf2word', 'pdf2excel', 'pdf2ppt'} | PHOTOS:
                suffix = options.get('suffix', suffix)
            require(ntpath.splitext(request['output'])[1].lower() == '.' + suffix, 'output_suffix_mismatch')
        else:
            basenames = [ntpath.basename(p).casefold() for p in inputs]
            stems = [ntpath.splitext(p)[0] for p in basenames]
            require(len(set(stems)) == len(stems), 'directory_input_basename_collision')
    windows_syntax(request.get('evidenceDir'))
    if 'wpscliPath' in request:
        windows_syntax(request['wpscliPath'])
        require(ntpath.basename(request['wpscliPath']).lower() == 'wpscli.exe', 'native_executable_name_required')
    if 'parentTimeoutSeconds' in request:
        number(request['parentTimeoutSeconds'], 1, 86400)
    if 'cloudProcessingAuthorized' in request:
        require(type(request['cloudProcessingAuthorized']) is bool, 'cloud_boolean_required')
    return command, options, shape


def resolve_binary(explicit=None):
    if explicit:
        path = winpath(explicit)
    else:
        found = shutil.which('wpscli.exe')
        if found:
            path = winpath(found)
        else:
            local = os.environ.get('LOCALAPPDATA', '')
            candidates = list((Path(local) / 'Kingsoft' / 'WPS Office').glob('*/clitool/wpscli.exe')) if local else []
            require(len(candidates) == 1, 'explicit_unique_wpscli_path_required')
            path = winpath(str(candidates[0]))
    require(path.is_file() and path.name.lower() == 'wpscli.exe', 'native_executable_missing')
    return path


def capture_process(argv, cwd, timeout):
    """只管理本次句柄；不查找或终止共享 WPS、孙进程或进程树。"""
    kwargs = {'cwd': str(cwd), 'shell': False, 'stdin': subprocess.DEVNULL,
              'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE}
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        p = subprocess.Popen(argv, **kwargs)
    except OSError:
        return {'exitCode': None, 'timedOut': False, 'spawned': False, 'error': 'spawn_failed'}, b'', b''
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        timed_out = True
        p.kill()  # exact handle returned by this invocation only
        try:
            out, err = p.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            out, err = e.output or b'', e.stderr or b''
            p.stdout.close()
            p.stderr.close()
            p.wait(timeout=2)
    return {'exitCode': p.returncode, 'timedOut': timed_out, 'spawned': True, 'pid': p.pid}, out, err


def observe_binary(binary, commands, cwd):
    identity = {'path': str(binary), 'size': binary.stat().st_size, 'sha256': sha_file(binary),
                'wrapperSha256': sha_file(__file__), 'help': {}}
    process, out, _ = capture_process([str(binary), '--version'], cwd, 15)
    version = out.decode('utf-8-sig', errors='replace').strip()
    require(process['exitCode'] == 0 and not process['timedOut'] and
            re.search(r'(?<![0-9.])' + re.escape(CLI_VERSION) + r'(?![0-9.])', version), 'native_version_mismatch')
    identity['version'] = version
    for command in commands:
        process, out, _ = capture_process([str(binary), command, '--help'], cwd, 15)
        actual = hashlib.sha256(out.replace(b'\r\n', b'\n')).hexdigest()
        require(process['exitCode'] == 0 and not process['timedOut'] and
                actual == CATALOG[command]['helpSha256'], 'native_help_mismatch')
        identity['help'][command] = actual
    require(sha_file(binary) == identity['sha256'], 'native_changed_during_probe')
    return identity


def build_argv(request, binary, inputs, output):
    command = request['command']
    argv = [str(binary), command] + [str(p) for p in inputs]
    if output is not None:
        argv += ['--output', str(output)]
    options = dict(request.get('options', {}))
    if 'image' in options:
        options['image'] = str(winpath(options['image']))
    if command != 'pdfinfo':
        options.setdefault('timeout', 300)
    if command == 'photo2pdf':
        options['overwrite'] = False
    for key, value in options.items():
        argv += ['--' + key, str(value).lower() if type(value) is bool else str(value)]
    for key, flag in (('password', '--password'), ('openPassword', '--open-pwd'), ('editPassword', '--edit-pwd')):
        if key in request:
            v = request[key]
            argv += [flag, ';'.join(v) if isinstance(v, list) else v]
    argv += ['--json']
    require(len(subprocess.list2cmdline(argv)) < 32767, 'windows_command_line_too_long')
    return argv


def parse_capture(data):
    """接受原生 JSONL 或既有 stdout 捕获封套，保留事件顺序与行号。"""
    text = data.decode('utf-8-sig', errors='replace') if isinstance(data, bytes) else data.lstrip('\ufeff')
    try:
        envelope = json.loads(text)
    except (ValueError, TypeError):
        envelope = None
    if isinstance(envelope, dict) and isinstance(envelope.get('stdout'), str):
        text = envelope['stdout'].lstrip('\ufeff')
    events, unknowns = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            unknowns.append('non_json_line:' + str(n))
            continue
        if not isinstance(e, dict):
            unknowns.append('non_object_event:' + str(n))
            continue
        events.append({'line': n, 'event': e})
        if e.get('type') not in {'started', 'progress', 'completed', 'error'}:
            unknowns.append('unknown_event_type:' + str(n))
    return events, unknowns


def normalized_name(value):
    return ntpath.normcase(ntpath.normpath(str(value)))


def bind_events(events, command, inputs, merged=False):
    rows, unknowns, active = [], [], None
    if command == 'pdfinfo':
        for item in events:
            e = item['event']
            if e.get('type') != 'completed':
                if e.get('type') == 'error':
                    unknowns.append('native_error')
                continue
            index = len(rows)
            okay = index < len(inputs) and normalized_name(e.get('file', '')) == normalized_name(inputs[index])
            okay = okay and type(e.get('page_count')) is int and e['page_count'] > 0 and type(e.get('is_scan_document')) is bool
            rows.append({'inputIndices': [index], 'status': 'success' if okay else 'unknown', 'completed': e})
            if not okay:
                unknowns.append('pdfinfo_binding_or_schema_unknown')
        if len(rows) != len(inputs):
            unknowns.append('missing_or_extra_pdfinfo')
        return rows, unknowns
    expected = 1 if merged else len(inputs)
    for item in events:
        e = item['event']; kind = e.get('type')
        if kind == 'started':
            index = len(rows)
            if active is not None or index >= expected:
                unknowns.append('unexplained_started_order')
            okay = index < expected
            if not merged:
                okay = okay and isinstance(e.get('input'), str) and normalized_name(e['input']) == normalized_name(ntpath.basename(str(inputs[index])))
            active = {'inputIndices': list(range(len(inputs))) if merged else [index], 'bindingVerified': bool(okay)}
        elif kind == 'completed':
            okay = active is not None and active['bindingVerified'] and e.get('status') == 'success'
            rows.append({**(active or {'inputIndices': [], 'bindingVerified': False}),
                         'status': 'success' if okay else 'unknown', 'completed': e})
            if not okay:
                unknowns.append('completion_unverified')
            active = None
        elif kind == 'error':
            unknowns.append('native_error')
            if active is not None:
                rows.append({**active, 'status': 'failed', 'error': e})
                active = None
    if active is not None or len(rows) != expected:
        unknowns.append('missing_or_extra_completion')
    return rows, unknowns


def checked_file(path, scope):
    path = Path(path)
    require(not path.is_symlink() and under(path, scope), 'artifact_escape')
    require(path.is_file() and path.stat().st_size > 0, 'missing_or_empty_artifact')
    return {'path': str(path.resolve()), 'size': path.stat().st_size, 'sha256': sha_file(path)}


def image_dimensions(path):
    """只读有限头部；尺寸不表示像素解码、页对应或渲染已通过。"""
    with path.open('rb') as f:
        raw = f.read(65536)
    try:
        if raw.startswith(b'\x89PNG\r\n\x1a\n') and raw[12:16] == b'IHDR':
            width, height = struct.unpack('>II', raw[16:24])
        elif raw.startswith(b'BM'):
            width, height = struct.unpack('<ii', raw[18:26]);height = abs(height)
        elif raw.startswith(b'\xff\xd8'):
            i = 2
            while i + 4 <= len(raw):
                require(raw[i] == 255, 'unrecognized_jpeg_header')
                marker = raw[i + 1]
                if marker == 255:
                    i += 1;continue
                size = int.from_bytes(raw[i + 2:i + 4], 'big')
                require(size >= 2, 'unrecognized_jpeg_header')
                if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
                    height, width = struct.unpack('>HH', raw[i + 5:i + 9]);break
                i += size + 2
            else:
                raise Rejected('jpeg_dimensions_not_in_bounded_header')
        else:
            raise Rejected('unsupported_dimension_header')
        require(width > 0 and height > 0, 'invalid_image_dimensions')
        return {'width': width, 'height': height, 'source': 'bounded-header-only', 'pageMapping': 'not-assessed'}
    except (Rejected, struct.error):
        return {'status': 'unverified', 'source': 'bounded-header-only'}


def markdown_resources(path, scope):
    text = path.read_text(encoding='utf-8-sig')
    refs = re.findall(r'!?\[[^\]\n]*\]\(\s*(<[^>]*>|[^\s)]+)(?:\s+[^)]*)?\)', text)
    for match in re.findall(r'<img\b[^>]*\bsrc\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', text, re.I):
        refs.append(next((v for v in match if v), ''))
    # Reference-style image/link destinations are also resources.
    refs += re.findall(r'^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)', text, re.M)
    resources, seen = [], set()
    for ref in refs:
        ref = urllib.parse.unquote(html.unescape(ref.strip('<>')))
        url = urllib.parse.urlsplit(ref)
        if not url.path and url.fragment:
            continue
        require(not url.scheme and not url.netloc and not ref.startswith(('/', '\\')) and
                not ntpath.isabs(ref) and not ntpath.splitdrive(ref)[0], 'markdown_external_resource')
        p = path.parent / url.path.replace('\\', '/')
        info = checked_file(p, scope)
        if info['path'] not in seen:
            resources.append(info); seen.add(info['path'])
    return resources


def zip_entries(path):
    with zipfile.ZipFile(path) as z:
        rows = []
        for info in z.infolist():
            n = info.filename.replace('\\', '/')
            require(not n.startswith('/') and not ntpath.splitdrive(n)[0] and
                    '..' not in n.split('/') and not (info.external_attr >> 16) & 0o170000 == 0o120000,
                    'zip_entry_escape')
            rows.append({'name': info.filename, 'size': info.file_size, 'crc': info.CRC})
        return rows


def collect_artifacts(rows, scope, command, resolver=None):
    resolver = resolver or winpath
    result, unknowns, seen, referenced = [], [], set(), set()
    try:
        stable_directory(scope)
    except (Rejected, OSError):
        return [], ['output_scope_identity_changed'], []
    for row in rows:
        completed = row.get('completed', {})
        if 'output' not in completed:
            unknowns.append('completed_output_missing'); continue
        outputs = completed['output']
        if isinstance(outputs, str):
            outputs = [outputs]
        if not isinstance(outputs, list) or not outputs or not all(isinstance(p, str) for p in outputs):
            unknowns.append('unexplained_output_shape'); continue
        for output in outputs:
            try:
                p = resolver(output)
                require(under(p, scope) and not p.is_symlink(), 'artifact_escape')
                candidates = list(tree_paths(p, scope)) if p.is_dir() else [p]
                files = [q for q in candidates if not q.is_dir()]
                require(bool(files), 'empty_output_directory')
                for q in candidates:
                    require(not q.is_symlink() and under(q, scope), 'artifact_escape')
                for q in files:
                    info = checked_file(q, scope)
                    if q.suffix.lower() in EXTENSIONS['photo']:
                        info['imageDimensions'] = image_dimensions(q)
                    if q.suffix.lower() == '.md':
                        resources = markdown_resources(q, scope)
                        info['resources'] = resources
                        referenced.update(x['path'] for x in resources)
                        for resource in resources:
                            if resource['path'] not in seen:
                                result.append(resource); seen.add(resource['path'])
                    if command == 'pdf2cad' and q.suffix.lower() == '.zip':
                        info['zipEntries'] = zip_entries(q)
                    if info['path'] not in seen:
                        result.append(info); seen.add(info['path'])
            except (Rejected, OSError, ValueError, zipfile.BadZipFile):
                unknowns.append('artifact_missing_unsafe_or_unreadable')
    # A fresh scope belongs to this run, but unanchored neighbors are not delivered artifacts.
    unbound = []
    try:
        for p in tree_paths(scope, scope):
            require(not p.is_symlink() and under(p, scope), 'artifact_escape')
            if p.is_file() and str(p.resolve()) not in seen:
                unbound.append(str(p.resolve()))
    except (Rejected, OSError):
        unknowns.append('output_scope_escape')
    return result, list(dict.fromkeys(unknowns)), unbound


def request_digest(request):
    return hashlib.sha256(json_bytes(request)).hexdigest()


def replay_record(run_dir, digest):
    if not run_dir.exists():
        return None
    try:
        intent = json.loads((run_dir / 'intent.json').read_text(encoding='ascii'))
        require(intent['requestDigest'] == digest, 'run_id_request_conflict')
    except Rejected:
        raise
    except (OSError, ValueError, KeyError):
        return {'status': 'unknown', 'replayed': False, 'unknowns': ['existing_incomplete_intent'], 'stateDir': str(run_dir)}
    result_path = run_dir / 'result.json'
    if result_path.is_file():
        try:
            r = json.loads(result_path.read_text(encoding='ascii'));r['replayed'] = False;r['existingRun'] = True
            return r
        except (OSError, ValueError):
            pass
    return {'status': 'unknown', 'replayed': False, 'requestDigest': digest,
            'unknowns': ['intent_exists_reconcile_without_replay'], 'stateDir': str(run_dir)}


def run_request(request):
    secrets = secret_values(request)
    require_windows()
    command, options, shape = validate_request(request)
    digest = request_digest(request)
    root = state_root()
    run_dir = root / request['runId']
    existing = replay_record(run_dir, digest)
    if existing is not None:
        return redact(existing, secrets)
    inputs = [winpath(p) for p in request['inputs']]
    for p in inputs:
        require(p.is_file() and p.stat().st_size <= 200 * 1024 * 1024, 'input_missing_or_too_large')
    auxiliary = []
    if 'image' in options:
        auxiliary.append(winpath(options['image']))
        require(auxiliary[0].is_file(), 'watermark_image_missing')
    protected = inputs + auxiliary
    require(len({str(p).casefold() for p in inputs}) == len(inputs), 'aliased_duplicate_input')
    hashes = [{'path': str(p), 'sha256': sha_file(p)} for p in protected]
    needs_cloud = command in PHOTOS or command in SCANNERS and (options.get('scanned', False) or options.get('ai-fix', False))
    if needs_cloud:
        require(request.get('cloudProcessingAuthorized') is True, 'explicit_cloud_authorization_required')
        safe_text(request.get('cloudAuthorizationBasis'))
        authorized = request.get('authorizedInputHashes')
        require(isinstance(authorized, dict) and authorized == {p: h['sha256'] for p, h in zip(request['inputs'], hashes)},
                'cloud_authorization_input_hash_mismatch')
    output = None if shape == 'none' else winpath(request['output'])
    scope = None if output is None else winpath(request.get('outputScope', request['output'] if shape == 'directory' else ntpath.dirname(request['output'])))
    evidence = winpath(request['evidenceDir'])
    for p in [evidence] + ([scope] if scope else []):
        require(not p.exists() and p.parent.is_dir(), 'fresh_scope_with_existing_parent_required')
        require(not overlaps(p, root) and not any(under(q, p) for q in protected), 'protected_path_overlap')
    if scope is not None:
        require((shape == 'file' and output.parent == scope) or (shape == 'directory' and output == scope), 'output_scope_shape_mismatch')
        require(not overlaps(evidence, scope), 'evidence_output_overlap')
        require(not any(output == p or under(p, scope) for p in protected), 'input_output_overlap')
    binary = resolve_binary(request.get('wpscliPath'))
    for p in (root, evidence, scope):
        if p is not None:
            require(not under(binary, p), 'binary_output_state_overlap')
    argv = build_argv(request, binary, inputs, output)
    root.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir(exist_ok=False)
    except FileExistsError:
        return redact(replay_record(run_dir, digest), secrets)
    intent = {'schemaVersion': 1, 'runId': request['runId'], 'requestDigest': digest, 'status': 'intent',
              'command': command, 'inputHashes': hashes, 'secretFieldsPresent': [k for k in SECRET_KEYS if k in request],
              'binary': {'path': str(binary), 'sha256': sha_file(binary)},
              'outputScope': str(scope) if scope else None, 'evidenceDir': str(evidence),
              'cloudAuthorizationBasis': request.get('cloudAuthorizationBasis') if needs_cloud else None}
    durable_json(run_dir / 'intent.json', intent, secrets, exclusive=True)
    result = {'schemaVersion': 1, 'runId': request['runId'], 'requestDigest': digest, 'status': 'unknown',
              'observedBinary': {**intent['binary'], 'wrapperSha256': sha_file(__file__), 'versionVerified': False},
              'process': {'spawned': False}, 'nativeEvents': [], 'commands': [], 'artifacts': [], 'unknowns': [],
              'verification': {'contentQuality': 'not-assessed'},
              'loadedConfiguration': {'stateRoot': str(root), 'stateDir': str(run_dir), 'evidenceDir': str(evidence),
                                      'outputScope': str(scope) if scope else None, 'dataTargets': [str(evidence)] + ([str(scope)] if scope else [])}}
    evidence_created = False
    evidence_identity = None
    try:
        evidence.mkdir(exist_ok=False);evidence_created = True
        evidence_identity = stable_directory(evidence)
        scope_identity = None
        if scope is not None:
            scope.mkdir(exist_ok=False)
            scope_identity = stable_directory(scope)
        identity = observe_binary(binary, [command], evidence)
        result['observedBinary'] = identity
        require(not scope or not any(scope.iterdir()), 'output_scope_no_longer_empty')
        require(all(sha_file(p) == h['sha256'] for p, h in zip(protected, hashes)), 'input_changed_before_launch')
        require(sha_file(binary) == intent['binary']['sha256'], 'binary_changed_before_launch')
        durable_json(run_dir / 'launched.json', {'status': 'launched', 'requestDigest': digest}, secrets, exclusive=True)
        timeout = request.get('parentTimeoutSeconds', min(86400, options.get('timeout', 300) * len(inputs) + 15))
        process, out, err = capture_process(argv, evidence, timeout)
        result['process'] = process
        stable_directory(evidence, evidence_identity)
        if scope is not None:
            stable_directory(scope, scope_identity)
        events, unknowns = parse_capture(out)
        merged = shape == 'file' and len(inputs) > 1
        rows, bindings = bind_events(events, command, inputs, merged)
        result.update(nativeEvents=events, commands=rows, stderr=err.decode('utf-8-sig', errors='replace'))
        result['unknowns'] += unknowns + bindings
        if process['timedOut']:
            result['unknowns'].append('timeout_effects_unknown')
        if process['exitCode'] != 0:
            exit_key = str(process['exitCode'])
            result['unknowns'].append('native_nonzero_or_spawn_failure')
            result['process']['documentedExit'] = CATALOG[command]['exitCodes'].get(exit_key, 'undocumented_exit')
        if scope is not None:
            artifacts, errors, unbound = collect_artifacts(rows, scope, command)
            result.update(artifacts=artifacts, unboundFiles=unbound)
            result['verification']['imageFileCount'] = sum(Path(a['path']).suffix.lower() in EXTENSIONS['photo'] for a in artifacts)
            result['unknowns'] += errors
            if not artifacts:
                result['unknowns'].append('no_verified_artifacts')
            for artifact in artifacts:
                if any(os.path.samefile(artifact['path'], p) for p in protected):
                    result['unknowns'].append('artifact_aliases_input')
        result['inputHashesAfter'] = [{'path': str(p), 'sha256': sha_file(p)} for p in protected]
        if result['inputHashesAfter'] != hashes:
            result['unknowns'].append('input_modified')
        if sha_file(binary) != identity['sha256']:
            result['unknowns'].append('binary_changed_during_run')
        if not result['unknowns'] and all(r['status'] == 'success' for r in rows):
            result['status'] = 'completed'
    except Exception as e:
        result['unknowns'].append(str(e) if isinstance(e, Rejected) else 'run_observation_or_storage_error')
    try:
        result['inputHashesAfter'] = [{'path': str(p), 'sha256': sha_file(p)} for p in protected]
        if result['inputHashesAfter'] != hashes:
            result['unknowns'].append('input_modified')
    except OSError:
        result['unknowns'].append('input_unreadable_after_run')
    if result['unknowns']:
        result['status'] = 'unknown'
    result['unknowns'] = list(dict.fromkeys(result['unknowns']))
    result = redact(result, secrets)
    durable_json(run_dir / 'result.json', result, secrets)
    if evidence_created:
        try:
            stable_directory(evidence, evidence_identity)
            durable_json(evidence / 'receipt.json', result, secrets, exclusive=True)
        except (OSError, Rejected):
            result['status'] = 'unknown';result['unknowns'].append('evidence_receipt_not_saved')
            durable_json(run_dir / 'result.json', result, secrets)
    return result


def probe(request):
    require_windows()
    require(isinstance(request, dict) and not set(request) - {'wpscliPath', 'commands'}, 'unknown_probe_field')
    commands = request.get('commands', list(CATALOG))
    require(isinstance(commands, list) and bool(commands) and all(isinstance(c, str) and c in CATALOG for c in commands), 'probe_command_not_allowed')
    binary = resolve_binary(request.get('wpscliPath'))
    return {'status': 'probe-matched', 'observedBinary': observe_binary(binary, commands, binary.parent),
            'scope': 'version/help only; no conversion, login, COM or GUI acceptance'}


def read_request(stream):
    data = stream.read(1024 * 1024 + 1)
    require(len(data) <= 1024 * 1024, 'request_too_large')
    def pairs(rows):
        r = {}
        for k, v in rows:
            require(k not in r, 'duplicate_json_key');r[k] = v
        return r
    try:
        return json.loads(data.decode('utf-8-sig'), object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise Rejected('invalid_utf8_json') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('catalog', help='只读 28 命令与原生选项目录，不启动子进程')
    actions.add_parser('probe', help='仅比对版本/帮助',
                       description='从 UTF-8 标准输入读取 JSON：可选 wpscliPath、commands 数组。仅运行 --version / 白名单命令 --help。')
    actions.add_parser('run', help='执行一个受限结构化请求',
                       description='从 UTF-8 标准输入读取 JSON：schemaVersion=1、runId、command、inputs、outputKind、evidenceDir；转换另需 output。options 按 catalog 声明；秘密只在标准输入临时提供。完整范围与恢复契约见 references/runtime.md。')
    action = parser.parse_args(argv).action
    request = {}
    try:
        if action == 'catalog':
            result = {'schemaVersion': 1, 'commandCount': len(CATALOG), 'commands': CATALOG,
                      'runtimePlatform': 'Windows', 'executionAccepted': False}
        else:
            request = read_request(sys.stdin.buffer)
            result = probe(request) if action == 'probe' else run_request(request)
        sys.stdout.buffer.write(json_bytes(redact(result, secret_values(request))))
        return 0 if result.get('status') != 'unknown' else 3
    except Exception as e:
        # Never stringify subprocess exceptions, which may carry secret argv.
        code = str(e) if isinstance(e, Rejected) else 'wrapper_error_reconcile_existing_intent'
        status = 'rejected' if isinstance(e, Rejected) or action != 'run' else 'unknown'
        sys.stdout.buffer.write(json_bytes(redact({'status': status, 'reason': code}, secret_values(request))))
        return 2 if status == 'rejected' else 3


if __name__ == '__main__':
    raise SystemExit(main())
