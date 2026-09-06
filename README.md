# yantrikdb-claude

A Claude Code plugin marketplace for [YantrikDB](https://github.com/yantrikos/yantrikdb) integrations.

## Plugins

| Plugin | What it does |
|--------|--------------|
| [`yantrikdb-hooks`](plugins/yantrikdb-hooks) | Deterministic YantrikDB memory: injects the boot digest at session start, recalls relevant memories on every prompt, and captures + consolidates before compaction and at session end. |

See [plugins/yantrikdb-hooks/README.md](plugins/yantrikdb-hooks/README.md) for install, configuration, and design notes.

## Install

```
/plugin marketplace add avbel/yantrikdb-claude
/plugin install yantrikdb-hooks@yantrikdb-claude
```

Or, for local development:

```bash
git clone https://github.com/avbel/yantrikdb-claude ~/src/yantrikdb-claude
claude --plugin-dir ~/src/yantrikdb-claude/plugins/yantrikdb-hooks
```

## Repository layout

```
.claude-plugin/marketplace.json   marketplace manifest
plugins/yantrikdb-hooks/          the plugin (hooks + plugin.json)
tests/                            unittest suite for the hooks
```

## Testing

```bash
python3 -m unittest discover -s tests -v
```

Unit tests need only the standard library. The end-to-end test drives the
hooks against a throwaway embedded YantrikDB store and skips itself when no
interpreter with `yantrikdb_mcp` installed can be found.

## License

MIT
