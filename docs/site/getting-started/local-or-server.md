# Local mode or a server

Search runs in one of two places. Client code is the same in both; only the line that creates the client changes.

| | Local mode | Server |
| --- | --- | --- |
| Where search runs | a child process on your CPU | a GPU server |
| Needs | `pip install "ombra[local]"` | Docker and a supported NVIDIA GPU |
| Speed | TBD, see [Performance](../performance/index.md) | TBD |
| Size | a few thousand vectors | larger collections |
| Default preset | fast | balanced |
| Use it for | trying Ombra, tests, small collections | production |

Local mode is not a plaintext shortcut: it encrypts and decrypts exactly as a server setup does, so code that works locally works against a server.

## Switch to a server

Start the server as in [Install](install.md) and create an API key as in [Docker](../operations/docker.md). The API key is an access token, unrelated to your secret key. Then replace `Client.local(...)` with `Client(address, api_key=..., root_certificates=...)`:

```python
client = Client(
    "localhost:50051",  # TLS is on unless you pass insecure=True (loopback test servers only)
    api_key=os.environ["OMBRA_API_KEY"],  # an access token, not your secret key
    root_certificates=Path("server.crt").read_bytes(),
)
```

The server refuses to start without TLS and an API key, except on a loopback test setup. `examples/server_search.py` is the quickstart against a server; run it as `python server_search.py HOST:PORT CA_CERT` with `OMBRA_API_KEY` and `OMBRA_PASSPHRASE` set. Against a server the default preset is "balanced": keys and ciphertexts are larger and creating the collection takes longer.

## What a server can learn

Ombra protects against a server that runs the software correctly but tries to learn what it can: the host, an insider, an attacker after a breach. It does not defend against a server that returns wrong results on purpose. In local mode the "server" is a child process on your machine, so this matters only for a real server.

The server cannot see vectors, queries, filter values or scores. It sees the collection size, the number of queries, their timing, and the document ids you fetch after the top-k. At the default Fast level it also sees which clusters a query searched, a hint at the topic. Decoy adds random clusters to hide the real ones, and Strict scans everything; both hide only the cluster choice, and the server still sees the candidate ids at every level. The server is also single-tenant: any read key reads every collection. Choose with `security_level=` at creation, or `level=` per search. See [What the server learns](../security/what-the-server-learns.md) and the [threat model](../security/threat-model.md).
