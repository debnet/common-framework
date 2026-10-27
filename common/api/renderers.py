# coding: utf-8
"""Rendu CSV optionnel des réponses DRF, activé si rest_framework_csv est installé."""

try:
    from rest_framework_csv.misc import Echo
    from rest_framework_csv.renderers import CSVRenderer

    class CustomCSVRenderer(CSVRenderer):
        """
        Rendu CSV pour les API paginées
        """

        results_field = "results"

        def render(
            self, data, media_type=None, renderer_context=None, writer_opts=None
        ):
            """Produit un générateur de lignes CSV pour une liste ou réponse paginée.

            renderer_context doit fournir request ; header, labels et writer_opts
            peuvent y surcharger la configuration. Le paramètre GET fields
            sélectionne les colonnes. Les cellules ne sont pas neutralisées
            contre les formules de tableur : exporter des données fiables.
            """
            if data is None:
                yield ""
            if not isinstance(data, list):
                data = data.get(self.results_field, data)
            if not isinstance(data, list):
                data = [data]

            header = renderer_context.get("header", self.header)
            labels = renderer_context.get("labels", self.labels)
            writer_opts = renderer_context.get(
                "writer_opts", writer_opts or self.writer_opts or {}
            )

            request = renderer_context.get("request")
            header = [
                field
                for field in request.query_params.get("fields", "").split(",")
                if field
            ] or header

            import csv

            csv_writer = csv.writer(Echo(), **writer_opts)
            for row in self.tablize(data, header=header, labels=labels):
                yield csv_writer.writerow([elem for elem in row])

except ImportError:
    pass
