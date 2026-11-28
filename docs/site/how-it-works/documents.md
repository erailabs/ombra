{% set B = const("ombra.blobstore.BUCKET_SIZES") %}
{% set OH = const("ombra.documents._OVERHEAD") %}
{% set PRE = const("ombra.documents._PREFIX") %}
{% set TH = const("ombra.documents.DEFAULT_INLINE_THRESHOLD") %}
{% set SC = const("ombra.keyfile.DEFAULT_SCRYPT") %}

# Documents and blobs

Every vector you add also produces encrypted blobs: one record blob with the exact vector and its metadata, and, if you pass a document, one document blob. The server stores them without being able to read them. The record blob is what makes [exact re-ranking](accuracy.md) possible.

## Envelope encryption

Blobs use AES-256-GCM, an authenticated cipher: decryption fails if a single byte changed. Each blob gets its own fresh random 256-bit key, wrapped under one long-lived key, the document master key (32 random bytes made with the client keys). The master key is stored with the OpenFHE secret key in the key file. The file is protected by a passphrase run through scrypt (N = {{ SC[0] }}, r = {{ SC[1] }}, p = {{ SC[2] }}, {{ 128 * SC[0] * SC[1] // 1048576 }} MiB of memory, random {{ const("ombra.keyfile.SALT_LEN") }} byte salt); the result encrypts both keys with AES-256-GCM, and the whole header is authenticated. The server never sees the master key, the blob keys or the passphrase.

Blob layout (version 1, integers little-endian):

| Field | Bytes |
| --- | --- |
| magic `OMBD` | 4 |
| version | 2 |
| nonce for the key wrap | 12 |
| wrapped blob key (32 byte key plus 16 byte tag) | 48 |
| nonce for the body | 12 |
| body: padded plaintext, AES-256-GCM, with its 16 byte tag | the rest |

That is {{ PRE }} bytes of header and a {{ OH-PRE }} byte tag: {{ OH }} bytes of overhead per blob. Inside the body, the plaintext starts with a mode byte (text, bytes, reference or record) and the true length, then the payload, then zero padding.

Both encryptions authenticate the blob's kind, collection name and id, and the body authenticates the whole header. A blob copied under another id or into another collection fails to open. The server can still return an older, valid version of the same blob ([what the server learns](../security/what-the-server-learns.md)).

## Size buckets

Blob length would leak how long a document is, so every stored blob has one of four sizes: {{ B[0] // 1024 }} KiB, {{ B[1] // 1024 }} KiB, {{ B[2] // 1048576 }} MiB or {{ B[3] // 1048576 }} MiB ({{ B[0] }}, {{ B[1] }}, {{ B[2] }}, {{ B[3] }} bytes). The client pads the plaintext with zeros to the smallest bucket $B$ that fits, with $n$ the payload plus the 9 byte inner header:

$$n + {{ OH }} \le B .$$

The server refuses a blob whose size is not a bucket size. A 768 dimension record (about 3.1 KB) goes in the {{ B[0] // 1024 }} KiB bucket. Most records therefore cost {{ B[0] // 1024 }} KiB: for 100,000 vectors about {{ (100000 * B[0] / 1e9) | round(1) }} GB of record blobs, before documents (bucket size times count, not measured on a real collection).

Within one bucket the server cannot tell a 100 byte document from a 15,000 byte one. Across buckets it can. The collection name, blob kind and the id you chose are also visible, so do not use an id that carries meaning, such as a file name or an email address.

## References to external files

A document body bigger than the inline threshold ({{ TH // 1048576 }} MiB by default) is stored as a reference: a small encrypted JSON with the file's `uri`, `sha256` and `size`. The file stays in your own storage, and the reference lands in the {{ B[0] // 1024 }} KiB bucket. On read, the client decrypts the reference, calls a fetch function you supply with the `uri`, and checks the result against the stored size and SHA-256. A mismatch raises an error. Ombra does not hide the request to your storage.

`Collection.add` does not create references yet: it raises an error for a body over the threshold. Use `DocumentCodec.seal_reference` and `fetch_document` directly.

## Record rows for private retrieval

A collection created with `private_retrieval=True` also stores one sealed row per vector next to every block, fetched with an encrypted call instead of by id ([Private record retrieval](private-retrieval.md)). A row holds the exact vector as float32 and, with range fields, every filter value and raw range value as int64, then the vector's metadata as compact JSON in a fixed slot (`metadata_bytes`), and the class and index of its document (below). The row holds no document body.

| Field | Bytes |
| --- | --- |
| nonce | 12 |
| AES-256-GCM of float32[dim] then int64[n_ints] | 4 dim + 8 n_ints |
| tag | 16 |
| zero padding to a multiple of 64 | the rest |

All rows of a collection have the same size, so the size shows only the dimension and the number of filter fields (3,136 bytes at 768 dimensions without range fields). The row key comes from the document master key through HKDF-SHA256 with its own label. The associated data binds the collection, cluster, id and a row version (1); a row copied elsewhere fails its tag and the client drops and counts it. Every row gets a fresh random nonce, and repacking a block seals its rows again.

## Private fetch of documents

For a private-retrieval collection the client also stores, per block and per size class, a document column: the sealed document blobs of the vectors whose blob is in that class, in block order, byte for byte the same as the blob stored by id. Two classes are fetched privately: 16 KiB (one plane) and 256 KiB (14 planes of 19,456 bytes). A search with `fetch_documents=True` makes one call per class over the same scope as the record call, and always asks for `k` rows. Which vector has which document is only inside the sealed record rows. A document in the 2 MiB or 16 MiB class stays a blob read by id; its hit is marked `large`. Collections made earlier have no document columns and keep working, as does `GetBlob`.

## Losing the keys

The document master key lives in the key file. Lose the file and the passphrase and every record and document is unreadable, as is the secret key. There is no recovery. Byte layouts: [file formats](../reference/file-formats/index.md).
