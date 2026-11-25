# Quickstart

This page encrypts a few sentences, searches them, and reads back the closest match. It runs in local mode, with a CPU server inside your Python process, so you need no GPU. Install first, see [Install](install.md):

```bash
pip install "ombra[local]" sentence-transformers
```

## Run it

The script embeds six sentences with `all-MiniLM-L6-v2` (384 dimensions, about 90 MB download on first run), encrypts them, and asks which one answers "What did the central bank do?". It is `examples/local_search.py`:

```python
"""First encrypted search in local mode: python local_search.py"""

import tempfile
import uuid
from pathlib import Path

from ombra import Client
from sentence_transformers import SentenceTransformer


def main() -> None:
    texts = [
        "The cat sat on the warm windowsill.",
        "Kittens love to chase a ball of yarn.",
        "Interest rates rose again this quarter.",
        "The central bank raised its policy rate.",
        "A GPU runs thousands of threads in parallel.",
        "Homomorphic encryption lets a server compute on data it cannot read.",
    ]
    ids = [str(uuid.uuid4()) for _ in texts]  # random ids: the server sees ids in the clear
    text_of = dict(zip(ids, texts, strict=True))

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")  # 384 dimensions
    vectors = model.encode(texts, normalize_embeddings=True).tolist()  # Ombra needs unit length

    workdir = Path(tempfile.mkdtemp())  # use a folder you keep for real data, e.g. "./ombra-data"
    client = Client.local(workdir / "server")  # a CPU server inside this process
    client.create_collection(
        "notes",
        vectors,
        ids=ids,
        key_file=workdir / "notes.key",  # holds the secret key: back it up, never share it
        passphrase="change this passphrase",
    )

    query = model.encode(["What did the central bank do?"], normalize_embeddings=True)[0].tolist()
    for hit in client.search("notes", query, k=3):
        print(f"{hit.score:.3f}  {text_of[hit.id]}")  # the score is the exact cosine similarity
    client.close()
    print(f"Keys and encrypted data are in {workdir}; delete it when you are done.")


if __name__ == "__main__":  # needed: local mode starts a child process
    main()
```

```bash
python local_search.py
```

Output looks like this, give or take the last digit:

```text
0.593  The central bank raised its policy rate.
0.333  Interest rates rose again this quarter.
0.133  A GPU runs thousands of threads in parallel.
```

Creating the collection (keys, encryption, upload) takes most of the run time. Search time is TBD, see [Performance](../performance/index.md).

## Things to know

- Ids are stored on the server in plain text, so use random ones. Never put names, e-mail addresses or file names in ids.
- `security_level` at creation decides which clusters a query asks for. The default is `fast`; `decoy` and `strict` hide more about the cluster choice, and only that: the client still fetches the best candidate records by id at every level. See [what the server learns](../security/what-the-server-learns.md).
- Vectors must have unit length. Scores are exact cosine similarities, higher is better.
- The `if __name__ == "__main__":` guard is required, because local mode starts a child process. Local mode is for trying Ombra, tests and a few thousand vectors, not production.
- The printed folder holds the secret key and the encrypted collection (a few hundred MB). Delete it when done.

Next: [Your first search](first-search.md)
