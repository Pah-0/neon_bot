from .defaults import ROLES_SEEN_VILLAGER, ROLES_SEEN_WOLF
from .roles import roles


class Player:
    def __init__(self, role, _id, user_id, name, jid=None):
        self.role = role  # Current role name
        self.original_role = role
        self.team = roles.get(role)[0]
        self.description = roles.get(role)[2]
        self.id = _id  # In-game numeric ID
        self.user_id = user_id  # WhatsApp user id
        self.jid = jid  # WhatsApp JID, used for DMs
        self.name = name

        self.is_alive = True
        self.is_injured = False
        self.is_lynched = False

        self.templates = []  # e.g., ["gunner", "cursed"]
        # List of strings for various states (compatibility with original
        # logic)
        self.other = []

        self.target = ""  # Night target
        self.vote = ""  # Day vote

        # Specific role variables
        self.totem = ""  # Totem to give (Shaman)
        self.totem_target = ""  # who the wolf shaman gives it to
        self.observe_target = ""  # who the werecrow watches
        self.bullet_count = 0
        self.last_target = ""
        self.loves = []  # List of user_ids
        self.cloning_target = None
        self.execution_target = None
        self.assassin_target = None  # the assassin template's target
        self.hidden_role = None  # what an amnesiac remembers being on night 3
        self.vote_at = 0.0  # when they last voted, for the desperation totem
        self.side = "villagers" if self.team == "villager" else "wolves"
        self.hexed = False
        self.sick = False
        self.is_cursed = False
        self.is_guarded = False
        self.is_entranced = False
        self.is_charmed = False

    @property
    def display_role(self):
        """What the seer sees; only the detective learns the true role"""
        if "cursed villager" in self.templates or self.role in ROLES_SEEN_WOLF:
            return "wolf"
        if self.role in ROLES_SEEN_VILLAGER:
            return "villager"
        return self.role

    @property
    def death_role(self):
        """The role shown when they die; a traitor is buried as a villager"""
        return "villager" if self.role == "traitor" else self.role

    @property
    def is_blessed(self):
        return "blessed villager" in self.templates or "blessed" in self.other

    @property
    def actual_team(self):
        # logic for turncoat, etc.
        if self.role == "turncoat":
            return "wolf" if "side:wolves" in self.other else "village"
        # roles use "villager" as the team name, win conditions use "village"
        return "village" if self.team == "villager" else self.team

    def add_other(self, state):
        if state not in self.other:
            self.other.append(state)

    def remove_other(self, state):
        if state in self.other:
            self.other.remove(state)

    def __repr__(self):
        return f"<Player {self.name} (#{self.id}) as {self.role}>"
