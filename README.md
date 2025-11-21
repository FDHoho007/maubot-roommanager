# Roommanager

Matrix introduced room creators in room version 12 and upwards for security reasons. The room creator is the user that created the room. The room creator has all permissions and cannot be changed.
For rooms that belong to an organization rather than a user this can be unpractical since each time a room creator leaves the organization, a new room needs to be created.
To model a room beloging to an organization, you rather want a user/bot representing the organization to own your rooms.

## Configuration

The plugin can be configured with instance administrators using the Web UI.
Instance administrators can use the `!becomeadmin` command and thereby claim permissions in any room owned by the room manager.

## List Rooms

```
!listrooms
```

This command lists all the rooms and spaces owned by the roommanager bot. The bot only owns rooms that are created by itself.

## Create Room

```
!createroom <public/private> <Room name> 
```

This command creates a new room (room version 12) with the name &lt;Room name&gt; and the bot user as room creator.
The visibility and join_rules can be set using either public or private.
After room creation the bot will invite you as an administrator into the room.

## Upgrade Room

```
!upgraderoom [Room ID]
```

This command can be used on existing rooms with a room version lower than the room version this bot uses (should be v12).
The bot will upgrade the room and thereby set himself as room creator.
All existing and pending members will be invited to the new room.
The bot user and the executing user need to be administrators of the existing room.
If this command receives no room id it will use the room the command was sent in.

While it is possible to upgrade spaces, most clients do not handle it gracefully. I therefore do not recommend upgrading spaces.

## Forget Room

```
!forgetroom [Room ID]
```

This command makes the bot leave and forget a room or space.
This only works, if the room is not created by the bot or has no other members except the bot.
While this command could also be used from within the room, rooms created by the bot can still only be forgotten when empty.
The executing user needs to be an instance administrator defined in the plugin config.

## Add Administrator

```
!addadmin <User ID> [Room ID]
```

This command makes another user an administrator in the given room.
If the user is not yet a member of the room he is invited before.
The executing user needs to be an administrator of the room.
If this command receives no room id it will use the room the command was sent in.

This can usually also be achieved by using a matrix client.

## Remove Administrator

```
!removeadmin <User ID> [Room ID]
```

This command demotes an administrator in the given room to a normal user.
The executing user needs to be an administrator of the room.
If this command receives no room id it will use the room the command was sent in.

This cannot be achieved using matrix clients or the api since all admins have the same permission level and cannot touch each other.
Only the room creator can remove administrators without them removing their permissions themselves.

## Become Administrator

```
!becomeadmin [Room ID]
```

This command promotes the executing user to and administrator of the room.
If the user is not yet a member of the room he is invited before.
The executing user does not have to have any permissions in the room yet, but needs to be an instance administrator defined in the plugin config.
If this command receives no room id it will use the room the command was sent in.

This command can be used by organization admins to claim permissions in rooms of their organization without needing another room administrator.
