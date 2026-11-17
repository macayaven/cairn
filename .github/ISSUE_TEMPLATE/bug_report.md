---
name: Something does not work
about: cairn did something other than what its guides or its pages say
labels: bug
---

**What happened**

**What you expected instead**

**How to see it**

The smallest steps that show it, from a fresh database if you can. Do not paste a real key, a real note or anyone's address: make the example up.

```
python -m cairn.server ...
```

**Where it runs**

- cairn: the commit or the image tag
- How you run it: Docker Compose (which file), `docker run`, or from a checkout with uv
- Operating system and Docker version, if Docker is involved

**What the server printed**

The lines around the failure from `docker compose logs cairn` or the terminal, with secrets removed.
