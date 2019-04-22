class Conversation:
    command_prefix = "!"

    def __init__(self, game, engine, xhr, version, challenge_queue, commands):
        self.game = game
        self.engine = engine
        self.xhr = xhr
        self.version = version
        self.challengers = challenge_queue
        self._commands = commands

        self._commands_string = f", {Conversation.command_prefix}".join(commands.keys())
        if self._commands_string != "":
            self._commands_string = f", {Conversation.command_prefix}" + self._commands_string

    def react(self, line, game):
        print("*** {} [{}] {}: {}".format(self.game.url(), line.room, line.username, line.text.encode("utf-8")))
        if line.text[:len(self.command_prefix)] == self.command_prefix:
            self.command(line, game, line.text[len(self.command_prefix):].lower())

    def command(self, line, game, cmd):
        if cmd == "commands" or cmd == "help":
            self.send_reply(line, "Supported commands: !name, !howto, !eval, !queue" + self._commands_string)
        if cmd == "wait" and game.is_abortable():
            game.abort_in(60)
            self.send_reply(line, "Waiting 60 seconds...")
        elif cmd == "name":
            self.send_reply(line, "{} (lichess-bot v{})".format(self.engine.name(), self.version))
        elif cmd == "howto":
            self.send_reply(line, "How to run your own bot: lichess.org/api#tag/Chess-Bot")
        elif cmd == "eval":
            if line.room == "spectator" or True:
                stats = self.engine.get_stats()
                self.send_reply(line, ", ".join(stats))
            else:
                self.send_reply(line, "I don't tell that to my opponent, sorry.")
        elif cmd == "queue":
            if self.challengers:
                challengers = ", ".join(["@" + challenger.challenger_name for challenger in reversed(self.challengers)])
                self.send_reply(line, "Challenge queue: {}".format(challengers))
            else:
                self.send_reply(line, "No challenges queued.")
        else:
            try:
                self.send_reply(line, self._commands[cmd])
            except KeyError:
                pass

    def send_reply(self, line, reply):
        self.xhr.chat(self.game.id, line.room, reply)


class ChatLine:
    def __init__(self, json):
        self.room = json.get("room")
        self.username = json.get("username")
        self.text = json.get("text")
