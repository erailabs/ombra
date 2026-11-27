# Client

`Client` and `AsyncClient` run the same client code (keys, encryption, clustering, top-k and re-ranking happen in this process); only the transport differs. The secret key stays in the key file named in `create_collection` and is never sent.

```python
from ombra import Client

client = Client("host:443", api_key="...")   # a GPU server (pip install ombra[grpc])
client = Client.local("./ombra-data")         # in-process CPU server (pip install ombra[local])
```

See [Local mode or a server](../../getting-started/local-or-server.md), [Errors, retries and deadlines](errors-and-retries.md) and [Results](results.md).

::: ombra.client.Client
    options:
      heading_level: 2
      show_root_heading: true
      show_source: false
      show_signature_annotations: true
      separate_signature: true
      docstring_section_style: table
      members_order: source
      merge_init_into_class: true
      show_if_no_docstring: true

::: ombra.client.AsyncClient
    options:
      heading_level: 2
      show_root_heading: true
      show_source: false
      show_signature_annotations: true
      separate_signature: true
      docstring_section_style: table
      members_order: source
      merge_init_into_class: true
      show_if_no_docstring: true

