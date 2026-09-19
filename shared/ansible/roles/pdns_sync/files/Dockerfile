FROM python:3.13.11-slim

RUN pip install --no-cache-dir \
    octodns==1.22.0 \
    octodns-netbox-dns==0.3.19 \
    octodns-powerdns==1.2.0

ENTRYPOINT ["octodns-sync"]
