ARG NETBOX_BASE_IMAGE
FROM ${NETBOX_BASE_IMAGE}

USER root
COPY local_requirements.txt /tmp/local_requirements.txt
COPY plugins.py /etc/netbox/config/plugins.py
RUN if ! python3 -m ensurepip --upgrade; then \
      python3 -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')" && \
      python3 /tmp/get-pip.py && \
      rm -f /tmp/get-pip.py; \
    fi
RUN . /opt/netbox/venv/bin/activate && pip install --no-cache-dir -r /tmp/local_requirements.txt
USER netbox
