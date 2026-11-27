# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""A test-only node that can search on the CPU.

The C++ `ombra_server` of the CPU build cannot search (no GPU). For tests with a router this script
serves the ombra.v1 gRPC API on top of local mode's `LocalServer` (client/ombra/_local.py), which
runs the server's own expansion and column dot product on the CPU. It is a test fixture only: it
has no TLS, one optional token, no aliases and no limits.

    python tests/cpu_node.py --data-dir D [--listen 127.0.0.1:0] [--token T] [--search-log F]
                              [--openfhe-version V]

It prints "ombra_server listening on HOST:PORT" once ready. With --search-log it appends one line
per SearchChosen with the cluster ids asked (synthetic cluster numbers only), so a test can check
which clusters a node was asked for; the scope of every RetrieveRecords start goes to
FILE.retrieve the same way. Needs ombra._native (OpenFHE 1.6.1).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent import futures

import grpc
from ombra._local import LocalServer
from ombra._proto import load_stubs

pb, pbg = load_stubs()


class Node(pbg.OmbraServicer):
    def __init__(self, data_dir: str, token: str, search_log: str, openfhe_version: str) -> None:
        # The envelopes carry openfhe-python's version, like the real server's --openfhe-version.
        self.srv = LocalServer(data_dir, pb, openfhe_version)
        self.token = token
        self.search_log = search_log

    def _auth(self, context: grpc.ServicerContext) -> None:
        if not self.token:
            return
        md = dict(context.invocation_metadata())
        if md.get("authorization") != f"Bearer {self.token}":
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing or invalid API key")

    def _unary(self, name: str, request, context):
        self._auth(context)
        return getattr(self.srv, name)(request)

    def _client_stream(self, name: str, request_iterator, context):
        self._auth(context)
        resp = getattr(self.srv, name)(request_iterator)
        if resp.HasField("error"):  # codes and fixed messages only, as the real server logs
            print(
                f"{name} refused: {resp.error.code} {resp.error.message}",
                file=sys.stderr,
                flush=True,
            )
        return resp

    def _server_stream(self, name: str, request, context):
        self._auth(context)
        yield from getattr(self.srv, name)(request)

    def Search(self, request_iterator, context):
        self._auth(context)

        def logged():
            for r in request_iterator:
                if self.search_log and r.WhichOneof("step") == "chosen":
                    with open(self.search_log, "a") as f:
                        f.write(" ".join(str(c) for c in r.chosen.cluster_ids) + "\n")
                yield r

        yield from self.srv.Search(logged())

    def RetrieveRecords(self, request_iterator, context):
        self._auth(context)

        def logged():
            for r in request_iterator:
                if self.search_log and r.WhichOneof("part") == "start":
                    st = r.start  # the scope only: synthetic cluster numbers
                    ids = "all" if st.all_clusters else " ".join(str(c) for c in st.cluster_ids)
                    with open(self.search_log + ".retrieve", "a") as f:
                        f.write(ids + "\n")
                yield r

        yield from self.srv.RetrieveRecords(logged())

    def RetrieveDocuments(self, request_iterator, context):
        self._auth(context)
        yield from self.srv.RetrieveDocuments(request_iterator)


for _n in (
    "CreateCollection GetCollection ListCollections DropCollection Delete ListBlocks DeleteBlobs"
).split():
    setattr(Node, _n, lambda self, req, ctx, _n=_n: self._unary(_n, req, ctx))
for _n in "UploadKeys Insert ReplaceBlock PutBlob".split():
    setattr(Node, _n, lambda self, it, ctx, _n=_n: self._client_stream(_n, it, ctx))
for _n in "GetBlock GetBlob".split():
    setattr(Node, _n, lambda self, req, ctx, _n=_n: self._server_stream(_n, req, ctx))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--listen", default="127.0.0.1:0")
    ap.add_argument("--token", default="")
    ap.add_argument("--search-log", default="")
    ap.add_argument("--openfhe-version", default="1.6.1.0")
    a = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)  # a failing handler shows its traceback in the log
    opts = [
        ("grpc.max_receive_message_length", 64 << 20),
        ("grpc.max_send_message_length", 64 << 20),
    ]
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16), options=opts)
    pbg.add_OmbraServicer_to_server(
        Node(a.data_dir, a.token, a.search_log, a.openfhe_version), server
    )
    port = server.add_insecure_port(a.listen)
    server.start()
    host = a.listen.rsplit(":", 1)[0]
    print(f"ombra_server listening on {host}:{port}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
