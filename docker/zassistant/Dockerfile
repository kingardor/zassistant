FROM nvidia/cuda:12.4.0-devel-ubuntu22.04 AS zassistant

# Add open GL libraries
RUN apt-get update && \
        DEBIAN_FRONTEND=noninteractive \
        apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libatomic1 \
        libgl1 \
        libnuma1 \
        pkg-config \
        python3-dev \
        python3-pip \
        python3-tk \
        redis-server \
        tree  \
        wget \
        && \
        rm -rf /var/lib/apt/lists/* && \
        apt autoremove
  
RUN pip3 install --no-cache-dir \
        opencv_python \
        customtkinter \
        redis

RUN pip3 install --no-cache-dir \
        langchain \
        langchain-community \
        langchain-huggingface \
        langchain-ollama \
        promptquality \
        chromadb \
        sentence-transformers \
        pypdf

RUN update-ca-certificates

# To get video driver libraries at runtime (libnvidia-encode.so/libnvcuvid.so)
ENV NVIDIA_DRIVER_CAPABILITIES $NVIDIA_DRIVER_CAPABILITIES,video,compute,graphics,utility

ENV CUDA_HOME=/usr/local/cuda
ENV CFLAGS="-I$CUDA_HOME/include $CFLAGS"
ENV RUNNING_IN_DOCKER true

ENV LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:/usr/local/lib
        
WORKDIR /app

COPY ollama-install.sh /app/ollama-install.sh
COPY ollama-pull.sh /app/ollama-pull.sh

RUN chmod +x ollama-install.sh && \
    ./ollama-install.sh && \
    chmod +x ollama-pull.sh && \
    ./ollama-pull.sh llama3.1:8b 

COPY hfembeddings-pull.sh /app/hfembeddings-pull.sh

RUN chmod +x hfembeddings-pull.sh && \
    ./hfembeddings-pull.sh

COPY zhp-info /app/zhp-info
COPY core /app/core
COPY zassistant.py /app/zassistant.py 
COPY run.sh /app/run.sh

RUN chmod +x run.sh

EXPOSE 11434
EXPOSE 6379

CMD ["./run.sh"]