"""Provider TLS uses system trust plus configured CA paths, with verification on."""
import ssl


def provider_ssl_context():
    # Passing SSL_CERT_FILE as an explicit cafile skips Windows default stores.
    # The default loader combines Windows trust with the configured OpenSSL paths.
    # Never fall back to verify=False when a configured CA path is unusable.
    context=ssl.create_default_context()
    if context.verify_mode!=ssl.CERT_REQUIRED or not context.check_hostname:
        raise ValueError('provider_tls_verification_required')
    return context
