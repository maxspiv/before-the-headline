import argparse

from flask import Flask, abort, jsonify, render_template, request

from demo_data import filter_records, load_dataset, replay_view, source_detail


def create_app(dataset=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=16384,
                      TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    app.json.ensure_ascii = False
    store = dataset if dataset is not None else load_dataset()

    def flag(name, default):
        values = request.args.getlist(name)
        if not values:
            return default
        if len(values) != 1 or values[0] not in ('0', '1'):
            abort(400)
        return values[0] == '1'

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/healthz')
    def health():
        return jsonify(status='ok', mode='cached-offline')

    @app.get('/api/evidence')
    def evidence():
        return jsonify(filter_records(
            store,
            include_unrelated=flag('unrelated', True),
            include_possible=flag('possible', True),
            fold_confirmed=flag('fold', False),
            include_uninspected=flag('inventory', False)))

    @app.get('/api/source/<source_id>')
    def source(source_id):
        try:
            return jsonify(source_detail(store, source_id))
        except KeyError:
            abort(404)

    @app.get('/api/replay')
    def replay():
        if len(request.args.getlist('day')) > 1:
            abort(400)
        try:
            return jsonify(replay_view(store, request.args.get('day', 'all')))
        except ValueError:
            abort(400)

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
