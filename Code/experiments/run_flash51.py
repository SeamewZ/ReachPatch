"""Persisted 51-case generation -> seal -> official evaluation workflow.

Only the public dataset and public base commits are used before generation
sealing. Any generation failure prevents entry into official evaluation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
import subprocess
import sys
import time
import traceback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    code = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(code))
    os.environ['REACHPATCH_RA51_ROOT'] = str(root)
    os.environ['REACHPATCH_SOURCE_TREE_ROOT'] = str(root / 'sources' / 'case_trees')
    os.environ['REACHPATCH_CASE_RETRIES'] = '1'
    os.environ['DEEPSEEK_MODEL'] = 'deepseek-flash'
    os.environ.pop('REACHPATCH_DIAGNOSTIC10', None)
    os.environ['PYTHONPATH'] = str(code)
    from experiments.reachavoid_51 import runner

    def status(phase, **details):
        payload = {'phase': phase, 'updated_at': runner.utc_now(), 'pid': os.getpid(),
                   'model': 'deepseek-flash', 'thinking': 'disabled', **details}
        runner._write_json(root / 'workflow_status.json', payload)
        with (root / 'workflow_events.jsonl').open('a') as handle:
            handle.write(json.dumps(payload) + '\n')
        print(json.dumps(payload), flush=True)

    def command(argv, cwd=None):
        with (root / 'preparation.log').open('a') as handle:
            subprocess.run(argv, cwd=cwd, check=True, timeout=600, stdout=handle, stderr=subprocess.STDOUT)

    phase = 'PREFLIGHT'
    try:
        status(phase)
        rows = runner._public_rows()
        original_hash = runner._implementation_hash()
        runner._write_json(root / 'workflow_config.json', {
            'model': 'deepseek-flash', 'thinking': 'disabled', 'temperature': 0,
            'seed': 'API_NOT_CONFIGURED', 'case_order': [r['instance_id'] for r in rows],
            'implementation_hash': original_hash, 'max_revisions': 8,
            'case_retries': 1, 'harness_workers': 2, 'harness_timeout_seconds': 1800,
            'official_evaluation_gate': 'ALL_51_PATCHES_SEALED',
        })
        if shutil.disk_usage(root).free < 200 * 1024 ** 3:
            raise RuntimeError('DISK_PREFLIGHT: less than 200 GiB free')
        phase = 'PREPARING_PUBLIC_BASES'
        local_repos = code / 'experiments/reachavoid_diagnostic10_exec_20260903_final/sources/repos'
        runner.SOURCE_TREE_ROOT.mkdir(parents=True, exist_ok=True)
        for index, row in enumerate(rows, 1):
            destination = runner.SOURCE_TREE_ROOT / row['instance_id']
            if destination.exists():
                try:
                    runner._source_tree(row)
                    continue
                except RuntimeError:
                    archive = root / 'source_preparation_failures'
                    archive.mkdir(exist_ok=True)
                    destination.rename(archive / (row['instance_id'] + '-' + str(time.time_ns())))
            status(phase, index=index, total=51, case_id=row['instance_id'])
            source = local_repos / row['repo'].split('/')[-1]
            local = source.is_dir() and subprocess.run(
                ['git', 'archive', row['base_commit']], cwd=source,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if local:
                command(['git', 'clone', '--shared', '--no-checkout', str(source), str(destination)])
            else:
                command(['git', 'init', str(destination)])
                command(['git', 'remote', 'add', 'origin', 'https://github.com/' + row['repo'] + '.git'], destination)
                command(['git', 'fetch', '--depth=1', 'origin', row['base_commit']], destination)
            command(['git', '-c', 'advice.detachedHead=false', 'checkout', '--detach', row['base_commit']], destination)
            runner._source_tree(row)
        key_path = Path('/home/slt/ReachPatch/ds_pwd.txt')
        runner._generation_preflight(rows, key_path)
        phase = 'PREPARING_EXECUTION_IMAGES'
        status(phase, total=51)
        def prepare_image(row):
            owner, name = row['repo'].split('/')
            image = f"swebench/sweb.eval.x86_64.{owner}_1776_{name}-{row['instance_id'].rsplit('-', 1)[-1]}:latest"
            inspect = subprocess.run(['docker', 'image', 'inspect', image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if inspect.returncode:
                if shutil.disk_usage(root).free < 100 * 1024 ** 3:
                    raise RuntimeError('DISK_IMAGE_PREPARATION: less than 100 GiB free')
                logs = root / 'image_preparation'
                logs.mkdir(exist_ok=True)
                with (logs / (row['instance_id'] + '.log')).open('a') as handle:
                    subprocess.run(['docker', 'pull', image], check=True, timeout=1800,
                                   stdout=handle, stderr=subprocess.STDOUT)
            if runner._execution_image(row) is None:
                raise RuntimeError('UNVERIFIED_EXECUTION_IMAGE: ' + row['instance_id'])
            return row['instance_id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(prepare_image, row) for row in rows]
            for index, future in enumerate(as_completed(futures), 1):
                status(phase, completed=index, total=51, case_id=future.result())
        phase = 'GENERATING_PATCHES'
        status(phase, total=51, implementation_hash=original_hash)
        generated = runner.generate(key_path, 'deepseek-flash', 8, set())
        if runner._implementation_hash() != original_hash:
            raise RuntimeError('IMPLEMENTATION_CHANGED_DURING_GENERATION')
        if generated.get('sealed_case_count') != 51 or not runner.SEALED_MANIFEST.is_file():
            raise RuntimeError('ALL_51_PATCHES_NOT_SEALED')
        phase = 'OFFICIAL_HARNESS'
        status(phase, sealed_cases=51)
        evaluated = runner.harness(workers=2, timeout=1800)
        phase = 'REPORTING'
        status(phase)
        report = runner.build_effectiveness_report()
        status('COMPLETE', p0_resolved=evaluated['p0']['resolved_instances'],
               final_resolved=evaluated['final']['resolved_instances'],
               report_path=str(root / 'component_effectiveness.json'))
        return 0
    except Exception as error:
        status('FAILED', failed_phase=phase, error_type=type(error).__name__, error=str(error))
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
