class Conversation:
    command_prefix = "!"
    username_prefix = "@"
    spectator_prefix = "spectator<"

    def __init__(self, game, engine, xhr, version, challenge_queue, commands, username):
        self.game = game
        self.engine = engine
        self.xhr = xhr
        self.version = version
        self.challengers = challenge_queue
        self._commands = commands
        self.username = username

        self._commands_string = ", {}".format(Conversation.command_prefix).join(commands.keys())
        if self._commands_string != "":
            self._commands_string = ", {}{}".format(Conversation.command_prefix, self._commands_string)

        self._username_string = "{}{} ".format(Conversation.username_prefix, username).lower()

    def react(self, line, game):
        print("*** {} [{}] {}: {}".format(self.game.url(), line.room, line.username, line.text.encode("utf-8")))
        if line.text[:len(self._username_string)].lower() == self._username_string and \
                line.room == "spectator":
            self.forward_to_private(line, line.text[len(self._username_string):])
        elif line.text[:len(Conversation.spectator_prefix)].lower() == Conversation.spectator_prefix and \
                line.room == "player" and line.username.lower() == self.username.lower():
            self.forward_to_public(line, line.text[len(Conversation.spectator_prefix):])
        elif line.text[:len(self.command_prefix)] == self.command_prefix:
            self.command(line, game, line.text[len(self.command_prefix):].split()[0].lower())

    def command(self, line, game, cmd):
        if cmd == "commands" or cmd == "help":
            self.send_reply(line, "Supported commands: !name, !howto, !eval, !queue, !chat{}.".format(
                self._commands_string
            ))
        if cmd == "wait" and game.is_abortable():
            game.abort_in(60)
            self.send_reply(line, "Waiting 60 seconds...")
        elif cmd == "name":
            # self.send_reply(line, "I am a chess engine (lichess-bot v{}).".format(self.version))
            self.send_reply(line, "{} (lichess-bot v{}).".format(self.engine.name(), self.version))
        elif cmd == "howto":
            self.send_reply(line, "How to run your own bot: lichess.org/api#tag/Chess-Bot")
        elif cmd == "eval":
            if line.room == "spectator" or line.username.lower() == self.username.lower():
                stats = self.engine.get_stats()
                if len(stats) == 0:
                    self.send_reply(line, "No evaluation reported.")
                else:
                    self.send_reply(line, ", ".join(stats) + ".")
            else:
                self.send_reply(line, "I don't tell that to my opponent, sorry.")
        elif cmd == "queue":
            if self.challengers:
                challengers = ", ".join(["@" + challenger.challenger_name for challenger in reversed(self.challengers)])
                self.send_reply(line, "Challenge queue: {}".format(challengers))
            else:
                self.send_reply(line, "No challenges queued.")
        elif cmd == "chat":
            self.send_reply(line, "You can chat with me (if I'm watching) by prepending messages with \"@{} \".".format(
                self.username
            ))
        else:
            try:
                self.send_reply(line, self._commands[cmd])
            except KeyError:
                pass

    def forward_to_private(self, line, text):
        line.room = "player"
        self.send_reply(line, "Message from {}: {}".format(line.username, text))

    def forward_to_public(self, line, text):
        line.room = "spectator"
        self.send_reply(line, text)

    def send_reply(self, line, reply):
        self.xhr.chat(self.game.id, line.room, reply)


class ChatLine:
    def __init__(self, json):
        self.room = json.get("room")
        self.username = json.get("username")
        self.text = json.get("text")
