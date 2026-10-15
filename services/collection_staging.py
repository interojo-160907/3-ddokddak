"""Private collector-process staging and recoverable publication of a data group.

Collectors run under the cross-process collection lock. No network operation
or calculation touches the published files until the entire group succeeds.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil


@contextmanager
def collector_paths(bindings):
    previous = []
    try:
        for module, values in bindings:
            for name, value in values.items():
                previous.append((module, name, getattr(module, name)))
                setattr(module, name, value)
        yield
    finally:
        for module, name, value in reversed(previous):
            setattr(module, name, value)


def copy_existing(source, destination):
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def recover_publication(journal):
    if not journal.exists():
        return
    entries = json.loads(journal.read_text('utf-8'))
    for entry in reversed(entries):
        target = Path(entry['target'])
        if entry['existed']:
            temporary = target.with_name(target.name + '.restore')
            shutil.copy2(entry['backup'], temporary)
            temporary.replace(target)
        else:
            target.unlink(missing_ok=True)
    journal.unlink()


def publish_group(pairs, folder, journal):
    """Rollback ordinary failures; leave a journal for interrupted publication."""
    entries = []
    backup = folder / 'before-publication'
    backup.mkdir(parents=True, exist_ok=True)
    for index, (source, target) in enumerate(pairs):
        if not source.is_file():
            raise FileNotFoundError(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        saved = backup / str(index)
        copy_existing(target, saved)
        entries.append(dict(target=str(target), backup=str(saved), existed=target.exists()))
    temporary = journal.with_suffix('.tmp')
    temporary.write_text(json.dumps(entries, ensure_ascii=False), encoding='utf-8')
    temporary.replace(journal)
    try:
        for source, target in pairs:
            temporary = target.with_name(target.name + '.publish')
            shutil.copy2(source, temporary)
            temporary.replace(target)
        journal.unlink()
    except BaseException:
        recover_publication(journal)
        raise


def prune_group_stages(snapshots, keep=3):
    """Bound large working copies; only remove directories owned by this module."""
    root = snapshots.resolve()
    folders = sorted((p for p in root.iterdir()
                      if p.is_dir() and not p.is_symlink() and not p.is_junction()
                      and (p / 'collection-group.json').is_file()),
                     key=lambda p: p.name, reverse=True)
    for folder in folders[keep:]:
        if folder.resolve().parent != root:
            continue
        try:
            shutil.rmtree(folder)
        except OSError:
            pass  # Windows reader: retry pruning on the next collection.
