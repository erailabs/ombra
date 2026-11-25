# Ombra

Ombra is a vector database where the vectors, the queries and the scores stay encrypted. The server stores and searches ciphertexts only, so it never sees your data or what you ask for. It uses homomorphic encryption (HE), and the search runs on GPUs.

## Who it is for

Teams that want similarity search over embeddings (documents, chat history, customer records) without trusting the host with the plain data. The cost: encrypted search is slower than plain search, and ciphertexts are much larger than vectors. Figures are TBD, see [Performance](performance/index.md).

Ombra works with embeddings of 384, 768 or 1536 dimensions. It has filters on integer fields, inserts, deletes and LangChain and LlamaIndex adapters. The server needs an NVIDIA GPU; a slow CPU local mode is there for trying it.

## Where to start

- To run something now: [Install](getting-started/install.md), then the [quickstart](getting-started/quickstart.md).
- To understand the ideas first: [what homomorphic encryption is](concepts/homomorphic-encryption.md).
- To see what the server can and cannot learn: the [threat model](security/threat-model.md).
- To see how the search works: [encoding](how-it-works/encoding.md).
- To see what is not built yet: the [roadmap](roadmap.md).

## License

Ombra is source-available. It is free for noncommercial use under the PolyForm Noncommercial License 1.0.0, and any business use needs a commercial license. The [README](https://github.com/erailabs/ombra#license) has the details.
