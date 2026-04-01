FROM node:20

ARG TZ
ENV TZ="$TZ"

ARG CLAUDE_CODE_VERSION=latest

# Install basic development tools and Python 3
RUN apt-get update && apt-get install -y --no-install-recommends \
  less \
  git \
  procps \
  sudo \
  fzf \
  zsh \
  man-db \
  unzip \
  gnupg2 \
  gh \
  jq \
  nano \
  vim \
  python3 \
  python3-venv \
  python3-pip \
  && apt-get clean && rm -rf /var/lib/apt/lists/* \
  && update-alternatives --install /usr/bin/python python /usr/bin/python3 1

# Ensure default node user has access to /usr/local/share
RUN mkdir -p /usr/local/share/npm-global && \
  chown -R node:node /usr/local/share

ARG USERNAME=node

# Persist bash history.
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
  && mkdir /commandhistory \
  && touch /commandhistory/.bash_history \
  && chown -R $USERNAME /commandhistory

# Set `DEVCONTAINER` environment variable to help with orientation
ENV DEVCONTAINER=true

# Create config directory and set permissions
RUN mkdir -p /home/node/.claude && \
  chown -R node:node /home/node/.claude

ARG GIT_DELTA_VERSION=0.18.2
RUN ARCH=$(dpkg --print-architecture) && \
  wget "https://github.com/dandavison/delta/releases/download/${GIT_DELTA_VERSION}/git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" && \
  sudo dpkg -i "git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" && \
  rm "git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb"

# Set up non-root user
USER node

# Install global packages
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH=$PATH:/usr/local/share/npm-global/bin

# Set the default shell to zsh rather than sh
ENV SHELL=/bin/zsh

# Set the default editor and visual
ENV EDITOR=nano
ENV VISUAL=nano

# Configure basic zsh without external dependencies
RUN echo 'autoload -U compinit && compinit' >> ~/.zshrc && \
  echo 'source /usr/share/doc/fzf/examples/key-bindings.zsh' >> ~/.zshrc && \
  echo 'source /usr/share/doc/fzf/examples/completion.zsh' >> ~/.zshrc && \
  echo "export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" >> ~/.zshrc

# Install Claude
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Configure pip mirror: Tencent Cloud primary, PyPI official as fallback
RUN pip3 config set global.index-url https://mirrors.cloud.tencent.com/pypi/simple/ && \
  pip3 config set global.extra-index-url https://pypi.org/simple/ && \
  pip3 config set global.trusted-host mirrors.cloud.tencent.com

# Install Python dependencies
RUN pip3 install --break-system-packages \
  claude-agent-sdk \
  requests

# 初始化 Claude CLI 配置，避免每次启动都执行登录
RUN mkdir -p /home/node/.claude && \
echo '{"hasCompletedOnboarding": true}' > /home/node/.claude.json

WORKDIR /app
ARG COMMIT_HASH
COPY --chown=node:node . /app
