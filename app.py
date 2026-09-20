import argparse
import json
import os
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from demo_data import filter_records, load_dataset, replay_view, source_detail
from investigations import (InvestigationImportError, Store, import_template,
                            DEFAULT_DATA_DIR, MAX_BODY_BYTES)

ROOT = Path(__file__).resolve().parent
PERSISTENCE_NOTE = ('Imported investigations are stored as JSON files on this '
                    'machine only. Nothing is uploaded.')


def create_app(data_dir=None, store=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=MAX_BODY_BYTES,
                      TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    app.json.ensure_ascii = False
    if store is None:
        if data_dir is None:
            data_dir = os.environ.get('SIGNAL_DATA_DIR') or DEFAULT_DATA_DIR
        store = Store(Path(data_dir), bundled=[load_dataset()])
    app.extensions['investigation_store'] = store

    def flag(name, default):
        values = request.args.getlist(name)
        if not values:
            return default
        if len(values) != 1 or values[0] not in ('0', '1'):
            abort(400)
        return values[0] == '1'

    def get_investigation(investigation_id):
        try:
            return store.get(investigation_id)
        except KeyError:
            abort(404)

    def investigation_meta(inv):
        return {'id': inv['id'], 'title': inv['title'],
                'story_label': inv['story_label'],
                'classification_note': inv['classification_note']}

    @app.get('/')
    def index():
        return render_template('home.html',
                               investigations=store.list(),
                               persistence_directory=str(store.data_dir),
                               persistence_note=PERSISTENCE_NOTE)

    @app.get('/investigations/<investigation_id>')
    def investigation_page(investigation_id):
        return render_template('investigation.html',
                               inv=get_investigation(investigation_id))

    @app.get('/import/schema')
    def import_schema():
        return render_template('schema.html')

    @app.get('/healthz')
    def health():
        return jsonify(status='ok', mode='cached-offline',
                       investigations=len(store.list()))

    @app.get('/api/investigations')
    def investigations_list():
        return jsonify(investigations=store.list(),
                       persistence={'mode': 'local-json-files',
                                    'directory': str(store.data_dir.resolve()),
                                    'note': PERSISTENCE_NOTE})

    @app.get('/api/investigations/<investigation_id>/evidence')
    def evidence(investigation_id):
        inv = get_investigation(investigation_id)
        view = filter_records(
            inv,
            include_unrelated=flag('unrelated', True),
            include_possible=flag('possible', True),
            fold_confirmed=flag('fold', False),
            include_uninspected=flag('inventory', False))
        view['investigation'] = investigation_meta(inv)
        return jsonify(view)

    @app.get('/api/investigations/<investigation_id>/source/<source_id>')
    def source(investigation_id, source_id):
        inv = get_investigation(investigation_id)
        try:
            detail = source_detail(inv, source_id)
        except KeyError:
            abort(404)
        detail['investigation'] = investigation_meta(inv)
        return jsonify(detail)

    @app.get('/api/investigations/<investigation_id>/replay')
    def replay(investigation_id):
        inv = get_investigation(investigation_id)
        if len(request.args.getlist('day')) > 1:
            abort(400)
        try:
            view = replay_view(inv, request.args.get('day', 'all'))
        except ValueError:
            abort(400)
        view['investigation'] = investigation_meta(inv)
        return jsonify(view)

    @app.post('/api/investigations/import')
    def import_investigation():
        if not request.is_json:
            return jsonify(errors=['Content-Type must be application/json']), 400
        try:
            inv = store.import_json(request.get_data())
        except InvestigationImportError as exc:
            return jsonify(errors=exc.errors), 400
        return jsonify(id=inv['id'], summary=_summary_for(inv)), 201

    def _summary_for(inv):
        return next(s for s in store.list() if s['id'] == inv['id'])

    @app.delete('/api/investigations/<investigation_id>')
    def delete_investigation(investigation_id):
        try:
            store.delete(investigation_id)
        except PermissionError as exc:
            return jsonify(error=str(exc)), 403
        except KeyError:
            abort(404)
        return Response(status=204)

    @app.get('/api/import/template')
    def import_template_download():
        body = json.dumps(import_template(), indent=2, ensure_ascii=False) + '\n'
        return Response(
            body, mimetype='application/json',
            headers={'Content-Disposition':
                     'attachment; filename='
                     'signal-noise-investigation-template.json'})

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(errors=['body: exceeds the %d-byte limit'
                               % MAX_BODY_BYTES]), 413

    @app.after_request
    def security(response):
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; font-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'none'")
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    app = create_app()
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
