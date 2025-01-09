# Usage Guide
Notes for later in case I need to revisit some manual process

### Updating Plex DB manually
On my old NAS, there's no `Plex SQLite` binary. This is required to run `UPDATE` queries on the Plex database. Trying to use standard SQLite results in the following error:
```
Execution finished with errors.
Result: unknown tokenizer: collating
```

So this process is required:
- download Plex Media Server locally (e.g. Mac distro)
- make sure NAS PMS is running same version (update if needed)
- put monitoring system in maintenance mode for plex (uptime kuma: pause)
- stop Plex process
- scp db file (`com.plexapp.plugins.library.db`) to local machine
- run SQLite binary inside local Server distro against this database: `~/Downloads/Plex\ Media\ Server.app/Contents/MacOS/Plex\ SQLite com.plexapp.plugins.library.db`
- run test query to verify db file is healthy, e.g.: `select title, added_at, originally_available_at from metadata_items where library_section_id = '1' limit 10;`
- issue queries to select and update (query files in this dir)
- scp db file back to NAS root
- backup db file in case something doesn't work
- replace old db file with updated
- start plex, verify, turn monitoring back on
