"""Explicitly authorized sealed subset evaluation and isolated failed-case retry."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
ROOT = CODE / 'experiments/reachavoid_flash51_20260916'
os.environ['REACHPATCH_RA51_ROOT'] = str(ROOT)
os.environ['REACHPATCH_SOURCE_TREE_ROOT'] = str(ROOT / 'sources/case_trees')
os.environ['REACHPATCH_CASE_RETRIES'] = '1'
os.environ.pop('REACHPATCH_DIAGNOSTIC10', None)
from experiments.reachavoid_51 import runner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('seal', 'evaluate', 'retry'))
    mode = parser.parse_args().mode
    subset = ROOT / 'harness' / 'subset47'
    seal_path = subset / 'sealed_subset.json'
    if mode == 'seal':
        if seal_path.exists():
            raise RuntimeError('Subset already sealed')
        rows = {row['instance_id']: row for row in runner._public_rows()}
        results = {p.stem: json.loads(p.read_text()) for p in runner.RESULT_ROOT.glob('*.json')}
        if len(results) != 47 or not all(runner._generation_result_valid(value, rows[key]) for key, value in results.items()):
            raise RuntimeError('Expected exactly 47 valid generation results')
        runner.HARNESS_ROOT = subset
        hashes = {kind: runner._seal_predictions(results, kind) for kind in ('p0', 'final')}
        runner._write_json(seal_path, {'instance_ids': sorted(results), 'case_count': 47,
            'prediction_hashes': hashes, 'implementation_hash': runner._implementation_hash(),
            'sealed_at': runner.utc_now(), 'authorization': 'User requested early evaluation of completed 47 cases'})
        print('SEALED_47', flush=True)
    elif mode == 'retry':
        seal = json.loads(seal_path.read_text())
        failed = {row['instance_id'] for row in runner._public_rows()} - set(seal['instance_ids'])
        if len(failed) != 4:
            raise RuntimeError('Expected four retry cases')
        runner.generate(Path('/home/slt/ReachPatch/ds_pwd.txt'), 'deepseek-flash', 8, failed)
    else:
        seal = json.loads(seal_path.read_text())
        for kind in ('p0', 'final'):
            prediction = subset / ('sealed_' + kind + '_predictions.jsonl')
            if runner._sha256(prediction) != seal['prediction_hashes'][kind]:
                raise RuntimeError('Sealed prediction hash mismatch')
        # Official data is read only by this evaluation-only process after
        # subset sealing. It is stored under harness, masked from generation.
        official = [row for row in runner._read_jsonl(runner.OFFICIAL_PATH)
                    if row['instance_id'] in seal['instance_ids']]
        if len(official) != 47:
            raise RuntimeError('Official subset mismatch')
        dataset = subset / 'official_subset.jsonl'
        runner._write_jsonl(dataset, official)
        for kind in ('p0', 'final'):
            stage = subset / kind
            stage.mkdir(exist_ok=True)
            runner._write_json(subset / 'evaluation_status.json', {'phase': kind, 'updated_at': runner.utc_now()})
            command = [sys.executable, '-m', 'swebench.harness.run_evaluation',
                '--dataset_name', str(dataset), '--split', 'test',
                '--predictions_path', str(subset / ('sealed_' + kind + '_predictions.jsonl')),
                '--max_workers', '2', '--timeout', '1800', '--run_id', 'flash47-' + kind + '-' + seal['prediction_hashes'][kind][:12],
                '--namespace', 'swebench', '--cache_level', 'instance', '--clean', 'False', '--report_dir', str(stage)]
            with (stage / 'harness.log').open('a') as log:
                subprocess.run(command, cwd=stage, stdout=log, stderr=subprocess.STDOUT, check=True)
        runner._write_json(subset / 'evaluation_status.json', {'phase': 'COMPLETE', 'updated_at': runner.utc_now()})


if __name__ == '__main__':
    main()
