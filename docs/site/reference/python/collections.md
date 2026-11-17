# Collections and key files

`ombra.collection` is the layer under `Client`: one collection on the server together with the client's keys for it. Most code uses [`Client`](client.md) instead. It also holds the key file handling (`KeyKit`). To back up and check key files from the shell, see [`ombra-keys`](../cli/ombra-keys.md).

::: ombra.collection
    options:
      heading_level: 2
      show_root_heading: false
      show_source: false
      show_signature_annotations: true
      separate_signature: true
      docstring_section_style: table
      members_order: source
      merge_init_into_class: true
      show_if_no_docstring: true

## Range filters

`Client.create_collection(range_fields=...)` and the filter bounds are described in [Filters](../../how-it-works/filters.md#range-fields). These helpers make the cut points.

::: ombra.ranges.quantile_edges
    options:
      heading_level: 3
      show_root_heading: true
      show_source: false
      show_signature_annotations: true
      separate_signature: true
      docstring_section_style: table
      members_order: source
      merge_init_into_class: true
      show_if_no_docstring: true

::: ombra.ranges.equal_width_edges
    options:
      heading_level: 3
      show_root_heading: true
      show_source: false
      show_signature_annotations: true
      separate_signature: true
      docstring_section_style: table
      members_order: source
      merge_init_into_class: true
      show_if_no_docstring: true

