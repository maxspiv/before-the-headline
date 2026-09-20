import contextlib
import hashlib
import io
import json
import socket
from pathlib import Path
from unittest.mock import patch

import historical_experiment as historical
import language_pages as pages
import language_recovery as recovery

ROOT = Path(__file__).resolve().parent
OUT = recovery.OUT


def digest_files(paths):
    return {str(path.relative_to(ROOT)): historical.sha(path) for path in sorted(paths)}


def run_analysis():
    historical.analyze()
    recovery.audit_translated()
    recovery.cached_window_estimates()
    recovery.verify_metadata_candidate(offline=True)
    pages.classify_pages()
    pages.summarize()
    recovery.final_findings()
    historical.preserve_demo()


def main():
    raw = list((ROOT / 'historical/raw').rglob('*.body')) + list((recovery.BASE / 'raw').rglob('*.body')) + list((recovery.BASE / 'pages').rglob('*.body'))
    before_raw = digest_files(raw)
    ledgers = [historical.LEDGER, recovery.STATE, pages.LEDGER]
    before_ledgers = digest_files(ledgers)
    artifacts = [historical.OUT / name for name in ('quality_report.json', 'gkg_observations.jsonl', 'distinct_documents.jsonl', 'mention_gkg_links.jsonl', 'record_id_collisions.jsonl')]
    artifacts += [OUT / name for name in ('translated_quality.json', 'translated_records.jsonl', 'corpus_transfer_estimates.json', 'metadata_candidate_samples.json', 'metadata_corpus_candidate.json', 'candidate_missing_object_probes.json', 'page_language_results.json', 'page_quality_summary.json', 'adjudicated_page_results.json', 'page_language_evidence.csv', 'failure_rates_by_publisher.csv', 'language_model.json', 'manual_review_plan.json', 'findings_summary.json', 'language_feasibility.svg', 'translated_nonrecord_lines.json')]
    attempts = []
    def blocked(*args, **kwargs):
        attempts.append('network_attempt')
        raise AssertionError('Offline verification prohibits socket/DNS/HTTP access')
    with patch('socket.socket.connect', blocked), patch('socket.socket.connect_ex', blocked), patch('socket.getaddrinfo', blocked), patch('urllib.request.OpenerDirector.open', blocked), contextlib.redirect_stdout(io.StringIO()):
        run_analysis()
        first = digest_files(artifacts)
        run_analysis()
        second = digest_files(artifacts)
    assert first == second, 'Derived outputs are not byte-reproducible'
    assert before_raw == digest_files(raw), 'Raw cache changed'
    assert before_ledgers == digest_files(ledgers), 'Transfer ledger changed during offline analysis'
    assert not attempts, 'A network attempt occurred'
    historical.preserve_demo()
    result = {'network_attempts': len(attempts), 'raw_response_bodies_protected': len(raw), 'derived_artifacts_compared': len(artifacts), 'two_cached_runs_byte_identical': True, 'raw_inputs_unchanged': True, 'transfer_ledgers_unchanged': True, 'demo_hashes_unchanged': True, 'derived_sha256': second, 'scope': 'Socket connections, DNS and urllib HTTP blocked in the replay process. System networking itself was not disabled. Classifier inputs are cached current-page bodies, not historical snapshots.'}
    historical.save(OUT / 'offline_verification.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'derived_sha256'}, indent=2))


if __name__ == '__main__':
    main()
