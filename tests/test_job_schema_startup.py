from concurrent.futures import ThreadPoolExecutor

from backend.app.storage.job_repository import JobRepository


def test_concurrent_first_requests_initialize_job_schema_once(tmp_path):
    path=tmp_path/'jobs.sqlite3'
    with ThreadPoolExecutor(max_workers=8) as pool:
        repositories=list(pool.map(lambda _:JobRepository(path),range(16)))
    for repository in repositories:
        assert repository.list_jobs()==[]
    with repositories[0]._connect() as connection:
        columns=[row['name'] for row in connection.execute('PRAGMA table_info(download_jobs)')]
    assert columns.count('request_key')==1
    assert columns.count('quality_json')==1
