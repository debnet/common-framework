# coding: utf-8
"""Transport websocket optionnel pour notifications, avec serveur Autobahn historique."""

import logging

from common.settings import settings

# Logging
logger = logging.getLogger(__name__)


# Vérifie que autobahn est bien installé
try:
    from autobahn.asyncio import websocket
except (ImportError, RuntimeError):
    try:
        from autobahn.twisted import websocket
    except ImportError:
        websocket = None


if websocket:

    class BroadcastServerProtocol(websocket.WebSocketServerProtocol):
        """
        Protocole de broadcasting par websocket
        """

        def onOpen(self):
            """Journalise et enregistre le client qui vient d'ouvrir sa connexion."""
            logger.info("=> {}".format(self.peer))
            self.factory.register(self)

        def onMessage(self, payload, isBinary):
            """Journalise puis diffuse le payload brut aux autres clients enregistrés."""
            logger.info("[{}] {}".format(self.peer, payload))
            self.factory.broadcast(payload, self)

        def onClose(self, wasClean, code, reason):
            """Journalise la fermeture ; aucun nettoyage de registre n'est réalisé ici."""
            logger.info("<= {}".format(self.peer))

    class BroadcastServerFactory(websocket.WebSocketServerFactory):
        """
        Service de broadcasting par websocket
        """

        def __init__(self, url, debug=False, debugCodePaths=False):
            super().__init__(url, debug=debug, debugCodePaths=debugCodePaths)
            self.clients = []

        def register(self, client):
            """Ajoute une connexion au registre local si elle n'y figure pas déjà."""
            if client not in self.clients:
                self.clients.append(client)

        def unregister(self, client):
            """Retire une connexion du registre local si elle est encore présente."""
            if client in self.clients:
                self.clients.remove(client)

        def broadcast(self, data, sender):
            """Transmet les données aux autres peers ; sans sender, aucun envoi.

            Il n'y a ni filtrage par utilisateur ni vérification de permission
            dans cette méthode. L'authentification du canal appartient au déploiement.
            """
            for client in self.clients:
                if sender and client.peer != sender.peer:
                    client.sendMessage(data)

    def run_websocket_server():
        """
        Démarre la boucle asyncio et le serveur Autobahn configurés par settings.

        Cette entrée bloque jusqu'à l'arrêt de la boucle ; ferme serveur et
        boucle en sortie. Requiert Autobahn et les settings WEBSOCKET_URL/HOST/PORT.
        Aucun contrôle d'authentification applicatif n'est ajouté à ce serveur.
        :return: Rien
        """
        import asyncio

        factory = BroadcastServerFactory(
            settings.WEBSOCKET_URL, debug=settings.WEBSOCKET_DEBUG
        )
        factory.protocol = BroadcastServerProtocol
        factory.setProtocolOptions()
        loop = asyncio.get_event_loop()
        server = loop.create_server(
            factory, settings.WEBSOCKET_HOST, settings.WEBSOCKET_PORT
        )
        server = loop.run_until_complete(server)
        try:
            loop.run_forever()
        except Exception:
            pass
        finally:
            server.close()
            loop.close()


def send_message(message):
    """
    Permet d'envoyer un message quelconque sur le même canal que le serveur de broadcasting par websocket
    :param message: Message
    :return: Rien
    """
    if not settings.WEBSOCKET_ENABLED:
        return
    try:
        import websocket

        ws = websocket.create_connection(settings.WEBSOCKET_URL)
        ws.send(message)
        ws.close()
    except ImportError:
        pass
