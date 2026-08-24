import src.main as main
from src.sources.google import GoogleSource


class GoogleHtmlResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


def test_google_source_extracts_jobs_from_js_callback(monkeypatch):
    html = """
    <html><body>
    <script class="ds:1" nonce="x">AF_initDataCallback({key: 'ds:1', hash: '2', data:[[ [
        ["121081200138691270","Strategy Associate, YouTube","https://www.google.com/about/careers/applications/signin?jobId=abc123&loc=US&title=Strategy+Associate",
        [null,"<ul><li><span>Break down problems</span></li></ul>"],
        [null,"<h3>Minimum qualifications:</h3><ul><li>Degree and experience.</li></ul>"]
    ] ]] ], sideChannel: {}});</script>
    </body></html>
    """
    monkeypatch.setattr("src.sources.google.requests.get", lambda *args, **kwargs: GoogleHtmlResponse(html))

    source = GoogleSource(
        company="Google",
        url="https://www.google.com/about/careers/applications/jobs/results/?location=United%20States&hl=en&jlo=en",
    )
    jobs = source.fetch()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "Google"
    assert job.external_id == "121081200138691270"
    assert job.title == "Strategy Associate, YouTube"
    assert job.location == "US"
    assert job.url.startswith("https://www.google.com/about/careers/applications/signin")
    assert "Break down problems" in job.description


def test_build_source_supports_google():
    source = main.build_source({"name": "Google", "type": "google", "query": "intern"})
    assert isinstance(source, GoogleSource)
