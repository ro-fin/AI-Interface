"""The .cameo package: files and folders as one stream, sent between devices and saved as a file.

Layout (every length big-endian):
    b'CAMEO\\x01' | 4-byte manifest length | manifest JSON | each file's bytes, in manifest order
    [b'CAMEOEND' | 4-byte trailer length | trailer JSON {'sha256': {path: hex}}]   (saved files only)
Manifest: {'v': 1, 'label', 'from', 'created', 'entries': [{'path': 'a/b.jpg', 'size', 'mime'} | {'path': 'a/', 'dir': True}]}

The sender never copies or zips first: Packer reads the sources straight into the stream and hashes each file as it
goes (one read of every byte). The receiver's Unpacker writes each file to its place as the bytes arrive, so a big
folder is already unpacked when the last chunk lands. Paths in a package are never trusted: see safe_rel().
"""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import struct
import time

MAGIC = b'CAMEO\x01'
TRAILER = b'CAMEOEND'
MAX_ENTRIES = 50_000
MAX_MANIFEST = 16 * 1024 * 1024
MAX_TOTAL = 8 * 1024 ** 3
_RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}


class PackageError(Exception):
    pass


def safe_rel(path):
    """A relative path from a package, as parts safe on Windows and Linux; PackageError if it could escape."""
    parts = []
    for part in str(path).replace('\\', '/').split('/'):
        if part in ('', '.'):
            continue
        if part == '..' or ':' in part:
            raise PackageError(f'unsafe path in package: {path!r}')
        part = ''.join(c for c in part if c.isprintable() and c not in '<>"|?*').rstrip(' .')
        if not part or part.split('.')[0].lower() in _RESERVED:
            raise PackageError(f'unsafe name in package: {path!r}')
        parts.append(part[:180])
    if not parts:
        raise PackageError('empty path in package')
    return parts


def category(mime):
    """Inbox sub-folder for a received file."""
    mime = mime or ''
    if mime.startswith('image/'):
        return 'Pictures'
    if mime.startswith('video/'):
        return 'Videos'
    if mime.startswith('audio/'):
        return 'Music'
    if mime in ('application/pdf', 'text/plain', 'text/csv') or 'document' in mime or 'officedocument' in mime or 'msword' in mime:
        return 'Documents'
    return 'Files'


def entries_for(paths):
    """Manifest entries and source files for the chosen files and folders. Folders keep their own name as the top
    level; links and junctions are skipped (they can point anywhere, even back into the folder)."""
    entries, sources, seen = [], [], set()
    for root in (Path(p) for p in paths):
        if root.is_symlink() or not root.exists():
            continue
        if root.is_file():
            name = root.name
            if name in seen:
                continue
            seen.add(name)
            entries.append({'path': name, 'size': root.stat().st_size, 'mime': mimetypes.guess_type(name)[0] or ''})
            sources.append(root)
            continue
        entries.append({'path': root.name + '/', 'dir': True})
        for folder, dirs, files in os.walk(root):
            folder = Path(folder)
            dirs[:] = sorted(d for d in dirs if not (folder / d).is_symlink() and not d.startswith('$'))
            rel = folder.relative_to(root.parent).as_posix()
            for d in dirs:
                entries.append({'path': f'{rel}/{d}/', 'dir': True})
            for f in sorted(files):
                p = folder / f
                if p.is_symlink() or not p.is_file():
                    continue
                entries.append({'path': f'{rel}/{f}', 'size': p.stat().st_size, 'mime': mimetypes.guess_type(f)[0] or ''})
                sources.append(p)
            if len(entries) > MAX_ENTRIES:
                raise PackageError(f'more than {MAX_ENTRIES} items')
    return entries, sources


class Packer:
    """The package for some files and folders as a stream of chunks. size is exact before the first chunk is read
    (the receiver checks free space with it); hashes are complete once the last chunk has been read."""

    def __init__(self, paths, label='', sender=''):
        self.entries, self.sources = entries_for(paths)
        files = [e for e in self.entries if not e.get('dir')]
        if not files:
            raise PackageError('nothing to send (no readable files)')
        manifest = {'v': 1, 'label': label or files[0]['path'], 'from': sender, 'created': time.time(), 'entries': self.entries}
        raw = json.dumps(manifest, separators=(',', ':')).encode()
        self.head = MAGIC + struct.pack('>I', len(raw)) + raw
        self.data_size = sum(e['size'] for e in files)
        if self.data_size > MAX_TOTAL:
            raise PackageError('larger than 8 GB')
        self.size = len(self.head) + self.data_size
        self.files = [(e, src) for e, src in zip(files, self.sources)]
        self.hashes = {}
        self.count = len(files)

    def chunks(self, size=1 << 20, hashes=None):
        """Yields the package in chunks of `size` bytes (the last may be shorter), filling `hashes` (default
        self.hashes) as each file completes: several streams of one Packer each pass their own dict. A source that
        changes while it is read would make the package lie about it, so that raises PackageError."""
        hashes = self.hashes if hashes is None else hashes
        buffer = bytearray(self.head)
        for entry, src in self.files:
            before = src.stat()
            if before.st_size != entry['size']:
                raise PackageError(f'{src.name} changed before it could be sent')
            digest, left = hashlib.sha256(), entry['size']
            with src.open('rb') as f:
                while left:
                    block = f.read(min(left, size))
                    if not block:
                        raise PackageError(f'{src.name} got shorter while it was sent')
                    digest.update(block)
                    left -= len(block)
                    buffer += block
                    while len(buffer) >= size:
                        yield bytes(buffer[:size])
                        del buffer[:size]
            after = src.stat()
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise PackageError(f'{src.name} changed while it was sent')
            hashes[entry['path']] = digest.hexdigest()
        if buffer:
            yield bytes(buffer)

    def trailer(self):
        raw = json.dumps({'sha256': self.hashes}, separators=(',', ':')).encode()
        return TRAILER + struct.pack('>I', len(raw)) + raw


class Unpacker:
    """Receives a package incrementally into `dest` (an empty staging folder). feed() takes bytes in any split;
    finish() checks that everything declared arrived. hashes maps each file's package path to its SHA-256."""

    def __init__(self, dest):
        self.dest = Path(dest)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.buffer = bytearray()
        self.manifest = None
        self.queue = []       # [(entry, target path)] files still to receive, in order
        self.current = None   # (entry, file, digest, left)
        self.hashes = {}
        self.trailer = None
        self.received = 0
        self.top = []         # top-level names created in dest, in order (what gets moved into the inbox)

    def _target(self, rel):
        parts = safe_rel(rel)
        target = self.dest.joinpath(*parts)
        if self.dest.resolve() not in target.resolve().parents:
            raise PackageError(f'unsafe path in package: {rel!r}')
        if parts[0] not in self.top:
            self.top.append(parts[0])
        return target

    def _read_manifest(self):
        if len(self.buffer) < len(MAGIC) + 4:
            return False
        if bytes(self.buffer[:len(MAGIC)]) != MAGIC:
            raise PackageError('not a Cameo package')
        (length,) = struct.unpack('>I', self.buffer[len(MAGIC):len(MAGIC) + 4])
        if length > MAX_MANIFEST:
            raise PackageError('manifest too large')
        start = len(MAGIC) + 4
        if len(self.buffer) < start + length:
            return False
        manifest = json.loads(bytes(self.buffer[start:start + length]))
        del self.buffer[:start + length]
        entries = manifest.get('entries')
        if manifest.get('v') != 1 or not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise PackageError('unsupported package')
        total = 0
        for entry in entries:
            target = self._target(entry.get('path', ''))
            if entry.get('dir'):
                target.mkdir(parents=True, exist_ok=True)
                continue
            size = entry.get('size')
            if not isinstance(size, int) or size < 0:
                raise PackageError('bad size in package')
            total += size
            self.queue.append((entry, target))
        if total > MAX_TOTAL:
            raise PackageError('package larger than 8 GB')
        self.manifest, self.data_size = manifest, total
        self.queue.reverse()
        return True

    def _next_file(self):
        entry, target = self.queue.pop()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise PackageError(f'{entry["path"]} appears twice in the package')
        self.current = [entry, target.open('wb'), hashlib.sha256(), entry['size']]

    def _close_current(self):
        entry, f, digest, _ = self.current
        f.close()
        self.hashes[entry['path']] = digest.hexdigest()
        self.current = None

    def feed(self, data):
        self.received += len(data)
        self.buffer += data
        if self.manifest is None and not self._read_manifest():
            return
        while self.buffer:
            if self.current is None:
                if not self.queue:
                    break
                self._next_file()
            entry, f, digest, left = self.current
            block = bytes(self.buffer[:left])
            del self.buffer[:len(block)]
            f.write(block)
            digest.update(block)
            self.current[3] = left = left - len(block)
            if left == 0:
                self._close_current()
        while self.current is None and self.queue and self.queue[-1][0]['size'] == 0:
            self._next_file()  # empty files have no bytes to wait for
            self._close_current()

    def done(self):
        return self.manifest is not None and self.current is None and not self.queue

    def finish(self):
        """After the last byte: every file complete, and the saved-file trailer (if any) checked."""
        if not self.done():
            raise PackageError('package ended early')
        if self.buffer:
            raw = bytes(self.buffer)
            if not raw.startswith(TRAILER) or len(raw) < len(TRAILER) + 4:
                raise PackageError('unexpected data after the package')
            (length,) = struct.unpack('>I', raw[len(TRAILER):len(TRAILER) + 4])
            self.trailer = json.loads(raw[len(TRAILER) + 4:len(TRAILER) + 4 + length])
            if self.trailer.get('sha256') != self.hashes:
                raise PackageError('SHA-256 mismatch: the package is damaged')
        return self.hashes

    def abort(self):
        if self.current:
            self.current[1].close()
            self.current = None


def unique(path):
    if not path.exists():
        return path
    for i in range(1, 1000):
        other = path.with_name(f'{path.stem} ({i}){path.suffix}')
        if not other.exists():
            return other
    return path.with_name(f'{path.stem}-{int(time.time())}{path.suffix}')


def install_into(unpacker, inbox):
    """Moves what an unpacker received from its staging folder into the inbox: a folder into Folders/, a file into
    the sub-folder for its type (Pictures, Videos, ...). Same drive, so this is a rename, not a copy. Returns the
    final paths in package order."""
    inbox = Path(inbox)
    mimes = {e['path']: e.get('mime', '') for e in unpacker.manifest['entries']}
    placed = []
    for name in unpacker.top:
        source = unpacker.dest / name
        if not source.exists():
            continue
        folder = inbox / ('Folders' if source.is_dir() else category(mimes.get(name) or mimetypes.guess_type(name)[0]))
        folder.mkdir(parents=True, exist_ok=True)
        target = unique(folder / name)
        os.replace(source, target)
        placed.append(target)
    return placed


def pack_file(paths, out, sender=''):
    """Saves files and folders as one .cameo file (with the checksum trailer). Returns the Packer."""
    packer = Packer(paths, sender=sender)
    out = Path(out)
    with out.open('wb') as f:
        for chunk in packer.chunks():
            f.write(chunk)
        f.write(packer.trailer())
    return packer


def unpack_file(path, dest):
    """Unpacks a saved .cameo file into the folder dest (normally a fresh staging folder). Returns the Unpacker."""
    unpacker = Unpacker(dest)
    try:
        with Path(path).open('rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                unpacker.feed(block)
        unpacker.finish()
    finally:
        unpacker.abort()
    return unpacker
