"""HTTP GET through QGIS's network stack (proxy/SSL settings honoured), with a urllib fallback."""


def http_get(url: str, timeout: int = 30) -> bytes:
    try:
        from qgis.core import QgsBlockingNetworkRequest
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest
    except ImportError:
        QgsBlockingNetworkRequest = None

    if QgsBlockingNetworkRequest is not None:
        req = QgsBlockingNetworkRequest()
        qreq = QNetworkRequest(QUrl(url))
        try:
            qreq.setTransferTimeout(int(timeout * 1000))
        except AttributeError:
            pass
        from .compat import enum
        err = req.get(qreq, forceRefresh=True)
        if err != enum(QgsBlockingNetworkRequest, "ErrorCode", "NoError"):
            raise RuntimeError(req.errorMessage() or f"network error {err}")
        reply = req.reply()
        status = reply.attribute(enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute"))
        if status is not None and int(status) >= 400:
            raise RuntimeError(f"HTTP {status} for {url}")
        return bytes(reply.content())

    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()
