import re
from typing import Type
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.api import Method, Path
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from mautrix.types import RoomDirectoryVisibility, Membership, EventType, TextMessageEventContent, PowerLevelStateEventContent, RoomType, RoomID, MessageType

ROOM_VERSION = "12"
EVENT_TYPE_ROOM_CHANGE = "ROOM_CHANGE"
EVENT_TYPE_PERMISSION_CHANGE = "PERMISSION_CHANGE"

class Config(BaseProxyConfig):
  def do_update(self, helper: ConfigUpdateHelper) -> None:
    helper.copy("administrators")
    helper.copy("silence_success_responses")
    helper.copy("logging_channel")
    helper.copy("logging_events")

class RoomManager(Plugin):
  
    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    async def start(self) -> None:
        self.config.load_and_update()

    @command.new(help="List all rooms owned by this Room Manager instance.")
    async def listrooms(self, evt: MessageEvent) -> None:
        rooms = {}
        for room in await self.client.get_joined_rooms():
            try:
                creation_event =[e for e in (await self.client.get_state(room)) if e.type == EventType.ROOM_CREATE][0]
                if creation_event.sender == self.client.mxid and creation_event.content.room_version == ROOM_VERSION:
                    rooms[room] = creation_event.content.type
            except Exception:
                continue
        if len(rooms) == 0:
            await evt.reply("No rooms created by this Room Manager instance were found.", allow_html=True)
        else:
            room_mentions = [f"{self.mention_mxid(r)} / {await self.get_room_name(r)} ({'Space' if rooms[r] == RoomType.SPACE else 'Room'})" for r in rooms.keys()]
            await evt.reply(f"Rooms created by this Room Manager instance:<br>" + "<br>".join(room_mentions), allow_html=True)

    @command.new(help="Creates a new room and adds you as an administrator.")
    @command.argument("visibility", label="public/private", matches=r"^(public|private)$", required=True)
    @command.argument("name", label="Room name", pass_raw=True)
    async def createroom(self, evt: MessageEvent, visibility: str, name: str) -> None:
        await self._createroom(evt, visibility, name)

    @command.new(help="Creates a new space and adds you as an administrator.")
    @command.argument("visibility", label="public/private", matches=r"^(public|private)$", required=True)
    @command.argument("name", label="Space name", pass_raw=True)
    async def createspace(self, evt: MessageEvent, visibility: str, name: str) -> None:
        await self._createroom(evt, visibility, name, creation_content={"type": "m.space"})

    async def _createroom(self, evt: MessageEvent, visibility: str, name: str, creation_content: dict|None = None) -> str:
        room_type = "room" if creation_content is None else "space"
        name = name.rstrip("<br/>").strip()
        if name == "":
            await evt.reply(f"Please provide a valid {room_type} name.")
            return
        is_public = visibility[0].rstrip("<br/>").strip() == "public"
        initial_state = [
            {
                "type": "m.room.encryption",
                "state_key": "",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"}
            },
            {
                "type": "m.room.join_rules",
                "state_key": "",
                "content": {"join_rule": "public" if is_public else "invite"}
            }
        ]
        room_id = (await self.client.create_room(
            alias_localpart=name.replace(" ", "-") if is_public else None, 
            visibility=RoomDirectoryVisibility.PUBLIC if is_public else RoomDirectoryVisibility.PRIVATE, 
            name=name, 
            invitees=[evt.sender],
            initial_state=initial_state,
            creation_content=creation_content,
            room_version=ROOM_VERSION, 
            power_level_override={"users": {evt.sender: 100}}
        ))
        if not self.config["silence_success_responses"] or not await self.is_group_chat(evt.room_id):
            await evt.reply(f"Created {room_type} {self.mention_mxid(room_id)} with visibility {visibility[0]}.", allow_html=True)
        await self.log_event(EVENT_TYPE_ROOM_CHANGE, f"{self.mention_mxid(evt.sender)} created the {room_type} {self.mention_mxid(room_id)} with visibility {visibility[0]}.")

    @command.new(help=f"Upgrade a room to version {ROOM_VERSION} (only for room admins).")
    @command.argument("room_id", label="Room ID", required=False)
    async def upgraderoom(self, evt: MessageEvent, room_id: str) -> None:
        room_id = self.parse_args(evt.content, evt.room_id, extract_user_id=False)

        # Check if the room exists and can be upgraded
        try:
            create_event = await self.client.get_state_event(room_id, EventType.ROOM_CREATE, "")
            if create_event.type != None:
                await evt.reply(f"The room {self.mention_mxid(room_id)} is a space. I can only upgrade rooms, not spaces.", allow_html=True)
                return
            if int(create_event.room_version) >= int(ROOM_VERSION):
                await evt.reply(f"The room {self.mention_mxid(room_id)} is already on version {create_event.room_version}. I currently uses room version {ROOM_VERSION}.", allow_html=True)
                return
        except Exception:
            await evt.reply(f"The room {self.mention_mxid(room_id)} does not exist or I am not a member of it.", allow_html=True)
            return
        
        # Check if the room has not yet been upgraded
        try:
            await self.client.get_state_event(room_id, EventType.ROOM_TOMBSTONE, "")
            await evt.reply(f"The room {self.mention_mxid(room_id)} has already been upgraded once and cannot be upgraded again.", allow_html=True)
            return
        except Exception:
            pass
        
        # Check that the user is an admin in the room
        try:
            room_members, power_levels = await self.get_room_members(room_id)
            if not evt.sender in self.config["administrators"]:
                await self.assert_room_admin(room_members, power_levels, evt.sender)
        except Exception as e:
            await evt.reply(e.args[0], allow_html=True)
            return

        try:
            new_room_id = (await self.client.api.request(Method.POST, Path.v3.rooms[room_id].upgrade, {"new_version": ROOM_VERSION}))["replacement_room"]
            members = [m.state_key for m in await self.client.get_members(room_id) if m.content.membership in [Membership.JOIN, Membership.INVITE]]
            for member in [m for m in members if not m == self.client.mxid]:
                await self.client.invite_user(new_room_id, member)
            if not self.config["silence_success_responses"] or not await self.is_group_chat(evt.room_id):
                await evt.reply(f"Room {self.mention_mxid(room_id)} has been upgraded to v{ROOM_VERSION}.", allow_html=True)
            await self.log_event(EVENT_TYPE_ROOM_CHANGE, f"{self.mention_mxid(evt.sender)} upgraded the room {self.mention_mxid(room_id)} to version {ROOM_VERSION}.")
        except Exception:
            await evt.reply(f"Could not upgrade the room {self.mention_mxid(room_id)}. Make sure I have sufficient permissions.", allow_html=True)

    @command.new(help="Forget an empty room (only for instance admins).")
    @command.argument("room_id", label="Room ID", required=False)
    async def forgetroom(self, evt: MessageEvent, room_id: str) -> None:
        room_id = self.parse_args(evt.content, evt.room_id, extract_user_id=False)
        
        if not evt.sender in self.config["administrators"]:
            await evt.reply("Only instance administrators can use this command. You can manage instance administrators via the maubot Web UI.", allow_html=True)
            return

        create_event = [e for e in await self.client.get_state(room_id) if e.type == EventType.ROOM_CREATE]
        if len(create_event) == 0:
            await evt.reply(f"The room {self.mention_mxid(room_id)} does not exist or I am not a member of it.", allow_html=True)
            return
        if create_event[0].sender != self.client.mxid:
            await self.client.leave_room(room_id)
            await self.client.forget_room(room_id)
            await evt.reply(f"I left the room {self.mention_mxid(room_id)} since I am not the owner.", allow_html=True)
            return
        
        members = [m for m in await self.client.get_members(room_id) if m.content.membership == Membership.JOIN]
        if len(members) > 1:
            await evt.reply(f"The room {self.mention_mxid(room_id)} is not empty. Only empty rooms can be forgotten.", allow_html=True)
            return
        await self.client.leave_room(room_id)
        await self.client.forget_room(room_id)
        await evt.reply(f"I have forgotten the empty room {self.mention_mxid(room_id)}.", allow_html=True)

    @command.new(help="Add another administrator to a given room (only for existing room admins).")
    @command.argument("user_id", label="User ID", required=True)
    @command.argument("room_id", label="Room ID", required=False)
    async def addadmin(self, evt: MessageEvent, user_id: str, room_id: str) -> None:
        room_id, user_id = self.parse_args(evt.content, evt.room_id)

        try:
            room_members, power_levels = await self.get_room_members(room_id)
            await self.assert_room_version(room_id)
            await self.assert_room_admin(room_members, power_levels, evt.sender)

            # Check if the user tries to promote the bot itself
            if user_id == self.client.mxid:
                raise Exception("I am already the room creator.")

            # If the user is not yet in the room, invite them
            try:
                if not user_id in room_members:
                    await self.client.invite_user(room_id, user_id)
            except Exception:
                raise Exception(f"Could not find user with ID {self.mention_mxid(user_id)} or invite failed.")

            # If the user is not yet an admin, promote them
            if power_levels.users.get(user_id, 0) < 100:
                power_levels.users[user_id] = 100
                await self.client.send_state_event(room_id, EventType.ROOM_POWER_LEVELS, power_levels)

            if not self.config["silence_success_responses"] or not await self.is_group_chat(evt.room_id):
                await evt.reply(f"User {self.mention_mxid(user_id)} has been promoted to administrator in room {self.mention_mxid(room_id)}.", allow_html=True)
            await self.log_event(EVENT_TYPE_PERMISSION_CHANGE, f"{self.mention_mxid(evt.sender)} promoted {self.mention_mxid(user_id)} to administrator in room {self.mention_mxid(room_id)}.")
        except Exception as e:
            await evt.reply(e.args[0], allow_html=True)
            return

    @command.new(help="Demotes a room administrator to normal user (only for room admins).")
    @command.argument("user_id", label="User ID", required=True)
    @command.argument("room_id", label="Room ID", required=False)
    async def removeadmin(self, evt: MessageEvent, user_id: str, room_id: str) -> None:
        room_id, user_id = self.parse_args(evt.content, evt.room_id)
        self.log.info(f"Demoting user {user_id} in room {room_id}")

        try:
            room_members, power_levels = await self.get_room_members(room_id)
            await self.assert_room_version(room_id)
            await self.assert_room_admin(room_members, power_levels, evt.sender)

            # Check if the user tries to demote the bot itself
            if user_id == self.client.mxid:
                raise Exception("I cannot be demoted.")

            # Check if the user is even a member
            if not user_id in room_members:
                raise Exception(f"The user {self.mention_mxid(user_id)} is not a member of the room.")

            # If the user is an admin, demote them to normal user
            if power_levels.users.get(user_id, 0) == 100:
                power_levels.users[user_id] = 0
                await self.client.send_state_event(room_id, EventType.ROOM_POWER_LEVELS, power_levels)

            if not self.config["silence_success_responses"] or not await self.is_group_chat(evt.room_id):
                await evt.reply(f"User {self.mention_mxid(user_id)} has been demoted from administrator in room {self.mention_mxid(room_id)}.", allow_html=True)
            await self.log_event(EVENT_TYPE_PERMISSION_CHANGE, f"{self.mention_mxid(evt.sender)} demoted {self.mention_mxid(user_id)} from administrator in room {self.mention_mxid(room_id)}.")
        except Exception as e:
            await evt.reply(e.args[0], allow_html=True)
            return

    @command.new(help="Promote yourself to a room administrator (only for instance admins).")
    @command.argument("room_id", label="Room ID", required=False)
    async def becomeadmin(self, evt: MessageEvent, room_id: str) -> None:
        room_id = self.parse_args(evt.content, evt.room_id, extract_user_id=False)

        try:
            room_members, power_levels = await self.get_room_members(room_id)
            await self.assert_room_version(room_id)

            # Assert that the sender is an instance admin
            if not evt.sender in self.config["administrators"]:
                raise Exception("Only instance administrators can use this command. You can manage instance administrators via the maubot Web UI.")

            # If the sender is not yet in the room, invite them
            if not evt.sender in room_members:
                await self.client.invite_user(room_id, evt.sender)

            # If the sender is not yet an admin, promote them
            if power_levels.users.get(evt.sender, 0) < 100:
                power_levels.users[evt.sender] = 100
                await self.client.send_state_event(room_id, EventType.ROOM_POWER_LEVELS, power_levels)

            if not self.config["silence_success_responses"] or not await self.is_group_chat(evt.room_id):
                await evt.reply(f"You have been promoted to administrator in room {self.mention_mxid(room_id)}.", allow_html=True)
            await self.log_event(EVENT_TYPE_PERMISSION_CHANGE, f"{self.mention_mxid(evt.sender)} promoted themselves to administrator in room {self.mention_mxid(room_id)}.")
        except Exception as e:
            await evt.reply(e.args[0], allow_html=True)
            return

    def parse_args(self, content: TextMessageEventContent, room_id: RoomID, extract_user_id: bool = True) -> str | tuple[str, str]:
        """Parses either room_id or room_id and user_id as arguments.
        If a mention is used and represented as an HTML link, the full Matrix ID is extracted.
        """
        command_raw = content.formatted_body if content.formatted_body else content.body
        command_raw = command_raw.strip()
        while command_raw.endswith("<br/>"):
            command_raw = command_raw[:-5].strip()
        # Mentioning a user can look like this: <a href="https://matrix.to/#/@user:server">@user</a>
        # Only the html variant contains the full Matrix ID, since the plain text variant removes the a tag and only keeps @user
        command_raw = re.sub(r'<a\s+href=[\'"]https?://matrix\.to/#/([^\'"]+)[\'"]>[^<]*</a>', r'\1', command_raw, flags=re.IGNORECASE)
        if extract_user_id:
            args = command_raw.split()
            #user_id = self.unpack_html_link("@", args[1])
            #room_id = self.unpack_html_link("#|!", args[2]) if len(args) > 2 else room_id
            return args[2] if len(args) > 2 else room_id, args[1]
        else:
            args = command_raw.split()
            #room_id = self.unpack_html_link("#|!", args[1]) if len(args) > 1 else room_id
            return args[1] if len(args) > 1 else room_id

    def unpack_html_link(self, prefix_token: str, text: str) -> str:
        # Mentioning a user can look like this: <a href="https://matrix.to/#/@user:server">@user</a>
        # Only the html variant contains the full Matrix ID, since the plain text variant removes the a tag and only keeps @user
        m = re.search(r'href=[\'"](https?://matrix\.to/#/(' + prefix_token + r'[^\'"]+))[\'"]', text)
        if m:
            return m.group(2)
        return text.rstrip("<br/>").strip()
    
    def mention_mxid(self, mxid: str) -> str:
        return f'<a href="https://matrix.to/#/{mxid}">{mxid}</a>'
    
    async def is_group_chat(self, room_id: str) -> bool:
        """Returns True if the room is a group chat (more than 2 members), False otherwise."""
        try:
            members = [m.state_key for m in await self.client.get_members(room_id) if m.content.membership == Membership.JOIN]
            return len(members) > 2
        except Exception:
            return False
        
    async def get_room_name(self, room_id: str) -> str:
        """Returns the room name for the given room ID."""
        try:
            room_state = await self.client.get_state(room_id)
            name_event = [e for e in room_state if e.type == EventType.ROOM_NAME]
            alias_event = [e for e in room_state if e.type == EventType.ROOM_CANONICAL_ALIAS]
            if len(name_event) > 0 and name_event[0].content.name:
                return name_event[0].content.name
            elif len(alias_event) > 0 and alias_event[0].content.canonical_alias:
                return alias_event[0].content.canonical_alias
            else:
                return "Unnamed Room"
        except Exception:
            return "Unknown Room"

    async def assert_room_version(self, room_id: str) -> None:
        """Asserts that the room is of the supported ROOM_VERSION and was created by the bot itself."""
        room_creation_event = [e for e in await self.client.get_state(room_id) if e.type == EventType.ROOM_CREATE][0]
        if room_creation_event.content.room_version != ROOM_VERSION:
            raise Exception(f"I only support rooms with version {ROOM_VERSION}. The room {self.mention_mxid(room_id)} has version {room_creation_event.content.room_version}.")
        if room_creation_event.sender != self.client.mxid:
            raise Exception(f"I only manage rooms created by myself. The room {self.mention_mxid(room_id)} was created by {self.mention_mxid(room_creation_event.sender)}.")

    async def assert_room_admin(self, room_members: list[str], power_levels: PowerLevelStateEventContent, user_id: str) -> None:
        """Asserts that the user is an admin in the room."""
        if user_id not in room_members:
            raise Exception(f"You are not a member of the room.")
        if power_levels.users.get(user_id, 0) < 100:
            raise Exception(f"You need to be an admin in the room yourself to perform this action.")

    async def get_room_members(self, room_id: str) -> tuple[list[str], PowerLevelStateEventContent]:
        """Returns the list of room members and the power levels dict for the given room."""
        try:
            room_members = [m.state_key for m in await self.client.get_members(room_id) if m.content.membership == Membership.JOIN]
            power_levels = await self.client.get_state_event(room_id, EventType.ROOM_POWER_LEVELS, "")
            return room_members, power_levels
        except Exception:
            raise Exception(f"The room {self.mention_mxid(room_id)} does not exist or I am not a member of it.")
    
    def strip_html_tags(self, text: str) -> str:
        return re.sub(re.compile('<.*?>'), '', text.replace('<br>', '\n'))

    async def log_event(self, event_type: str, message: str) -> None:
        """Logs an event to the logging channel if configured."""
        if self.config["logging_channel"] and event_type in self.config["logging_events"]:
            await self.client.send_message(
                self.config["logging_channel"],
                TextMessageEventContent(
                    msgtype=MessageType.TEXT,
                    body=self.strip_html_tags(message),
                    format="org.matrix.custom.html",
                    formatted_body=message
                )
            )
        