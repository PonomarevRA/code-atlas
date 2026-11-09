"""Local review records independent of temporary checkout expiration."""

from collections import Counter
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from uuid import uuid4

from .change_review_facts import compare_functions
from .map_storage import checked_path, identifier
from .source_viewer import SourceError, _read_regular_file
from .store import StoreError

DIRECTORY = 'change-reviews'
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_REVIEWS = 128


class ChangeReviews:
    """Persist the baseline, then atomically publish one immutable result per ID."""

    def __init__(self, data_dir):
        """Use the application data root without touching storage at startup."""
        self.data_dir = Path(data_dir)
        self.lock = RLock()

    def _path(self, review_id):
        """Only a server-issued opaque ID can select a file inside review storage."""
        try:
            return checked_path(self.data_dir, DIRECTORY, identifier(review_id, snapshot=True) + '.json',
                                directory=False)
        except ValueError as error:
            raise StoreError('Хранилище обзоров недоступно.', 503) from error

    def _read(self, review_id):
        """Read the owned record without following links or loading unbounded data."""
        path = self._path(review_id)
        try:
            payload = _read_regular_file(self.data_dir, path.relative_to(self.data_dir), MAX_RECORD_BYTES)
        except SourceError as error:
            status = 404 if error.status == 404 else 503
            raise StoreError('Обзор отсутствует или недоступен.', status) from error
        try:
            record = json.loads(payload)
            if record.get('version') != 1 or record.get('reviewId') != review_id:
                raise ValueError('Unsupported review record')
        except (ValueError, AttributeError, RecursionError) as error:
            raise StoreError('Сохранённый обзор повреждён.', 503) from error
        return record

    def _write(self, record):
        """Replace the record only after the complete bounded payload is on disk."""
        path = self._path(record['reviewId'])
        payload = json.dumps(record, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        if len(payload) > MAX_RECORD_BYTES:
            raise StoreError('Обзор превышает допустимый объём.', 413)
        temporary = None
        try:
            path.parent.mkdir(mode=0o700, exist_ok=True)
            with NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise StoreError('Не удалось сохранить обзор изменений.', 503) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def begin(self, project_id, owner_key, baseline, title):
        """Store the captured baseline and return its local review link."""
        review_id = uuid4().hex
        record = {'version': 1, 'reviewId': review_id, 'projectId': project_id,
                  'ownerKey': owner_key, 'title': title or 'Обзор изменений',
                  'before': baseline, 'after': None}
        with self.lock:
            directory = self._path(review_id).parent
            if sum(1 for _ in directory.glob('*.json')) >= MAX_REVIEWS:
                raise StoreError('Достигнут предел локальных обзоров изменений.', 429)
            self._write(record)
        return self.summary(record)

    def baseline(self, review_id, project_id, owner_key, snapshot_id):
        """Validate ownership and expose the saved entry identity for the second capture."""
        record = self._read(review_id)
        if (record['projectId'], record['ownerKey']) != (project_id, owner_key):
            raise StoreError('Для обзора нужна карта того же checkout и задачи.', 409)
        if record['after']:
            if record['after']['snapshotId'] == snapshot_id:
                return record, True
            raise StoreError('Этот обзор уже завершён другим снимком.', 409)
        if record['before']['snapshotId'] == snapshot_id:
            raise StoreError('Перестройте карту checkout после правок и укажите новый снимок.', 409)
        return record, False

    def finish(self, review_id, project_id, owner_key, after):
        """Commit once and treat a retry with the same snapshot as a read."""
        with self.lock:
            record, complete = self.baseline(review_id, project_id, owner_key, after['snapshotId'])
            if not complete:
                changes = compare_functions(record['before'], after)
                record['counts'] = dict(Counter(row['kind'] for row in changes))
                record['changes'] = changes
                record['after'] = after
                # Completed records only need the comparison and visible evidence.
                for side in (record['before'], after):
                    side.pop('functions')
                    side.pop('ambiguous')
                    side.pop('failedFiles', None)
                self._write(record)
        return self.summary(record)

    @staticmethod
    def summary(record):
        """Expose safe metadata and the link without returning the function inventory."""
        result = {'reviewId': record['reviewId'], 'title': record['title'],
                  'url': '/change-review.html?id=' + record['reviewId'],
                  'status': 'complete' if record['after'] else 'baseline',
                  'counts': record.get('counts', {}), 'totalChanges': len(record.get('changes', []))}
        for name in ('before', 'after'):
            side = record[name]
            result[name] = None
            if side is not None:
                result[name] = {key: side[key] for key in ('snapshotId', 'capturedAt', 'coverage', 'entry')}
                result[name]['snapshotCreatedAt'] = side.get('snapshotCreatedAt')
        return result

    def read(self, review_id, offset, limit, include_paths):
        """Page stable rows and optionally include both bounded static paths."""
        record = self._read(review_id)
        result = self.summary(record)
        rows = record.get('changes', [])
        result['changes'] = rows[offset:offset + limit]
        next_offset = offset + len(result['changes'])
        result['nextOffset'] = next_offset if next_offset < len(rows) else None
        if include_paths:
            result['paths'] = {name: record[name]['path'] if record[name] else None
                               for name in ('before', 'after')}
        return result
