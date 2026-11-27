# Your first search

This page walks through a small encrypted collection and what each call does. It builds on the [quickstart](quickstart.md) and adds filters, inserts and deletes, from `examples/filters_and_updates.py`. The code runs in local mode and works the same against a server, see [Local mode or a server](local-or-server.md).

## 1. Embed and create

Ombra does not compute embeddings. Bring vectors of 384, 768 or 1536 dimensions, normalized to unit length. Each vector needs an id. The server stores ids in plain text and sees them on every insert, delete and search, so use random ids and keep your own map from id to text.

```python
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
```

Creating a collection generates the keys, groups the vectors into clusters, encrypts them, and uploads the ciphertexts and public evaluation keys, all on your machine. The secret key goes to the key file and stays there. The passphrase protects that file, so use a real one. Local mode uses the "fast" preset and a server "balanced" ([presets](../reference/presets.md)). You cannot choose ring sizes or moduli, and no preset is below 128-bit security.

## 2. Search

```python
query = model.encode(["What did the central bank do?"], normalize_embeddings=True)[0].tolist()
for hit in client.search("notes", query, k=3):
    print(f"{hit.score:.3f}  {text_of[hit.id]}")  # the score is the exact cosine similarity
```

The client encrypts the query. The server scores it against every encrypted vector and returns encrypted scores. The client decrypts them, picks the top `k` and re-ranks those candidates exactly. A hit has an `id` and a `score`.

## 3. Filter

Filters work on integer fields you declare at creation. Here `topic` takes the values 0, 1 and 2. Filter values are encrypted like everything else; see [Filters](../how-it-works/filters.md).

```python
client.create_collection(
    "notes",
    embed(texts),
    ids=ids,
    key_file=key_file,
    passphrase="change this passphrase",
    metadata=[{"topic": t} for t in topics],
    filter_fields={"topic": 3},  # the field "topic" takes the values 0, 1 and 2
)

query = embed(["What did the central bank do?"])[0]
show("no filter", client.search("notes", query, k=3))
show("only topic 2 (tech)", client.search("notes", query, k=2, filter={"topic": 2}))
```

## 4. Insert and delete

```python
new_text, new_id = "Bond yields fell after the announcement.", str(uuid.uuid4())
info[new_id] = (new_text, 1)
client.insert("notes", embed([new_text]), ids=[new_id], metadata=[{"topic": 1}])
show("after an insert, topic 1", client.search("notes", query, k=3, filter={"topic": 1}))

client.delete("notes", [ids[3]])  # the fourth text, "The central bank raised its policy rate."
```

An insert encrypts the new vector on your machine and adds it. A delete hides the vector from results at once; the space comes back when the collection is compacted ([deletes](../how-it-works/deletes.md)).

## 5. Come back later

You need the server folder, the key file and the passphrase. Name the filter fields in creation order.

```python
client = Client.local(workdir / "server")
client.open_collection(
    "notes", key_file=key_file, passphrase="change this passphrase", filter_fields=["topic"]
)
```

## Your key file

The key file holds your secret key, protected by the passphrase, and the key that protects stored documents. If you lose the file or the passphrase, nobody can recover the data. Never send the file to a server or commit it. The server receives only ciphertexts and public evaluation keys. Anyone with both the file and the passphrase can read your data. See [Keys](../concepts/keys.md) and [what the server learns](../security/what-the-server-learns.md).
