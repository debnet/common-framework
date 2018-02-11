# coding: utf-8
import logging

from django.core.management.base import BaseCommand


# Logging
logger = logging.getLogger(__name__)


class ImportExcelCommand(BaseCommand):
    """
    Commande Django de base pour les imports Excel
    """
    workbook = None
    models = {}

    def importer(self, sheet, model=None, fields=None, keys=None, actions=None, ordre=None, force=False):
        """
        Permet d'importer les données d'une feuille Excel
        :param sheet: Libellé de la feuille dans le classeur
        :param model: Classe du modèle lié
        :param fields: Liste des champs ordonnées par rapport à la feuille
        :param keys: Clé de l'instance du modèle
        :param actions: Action spécifiques à exécuter pour un ou plusieurs champs donnés
        :param ordre: Nom de la propriété d'ordre dans le modèle
        :param force: Force l'import de données identiques
        :return: Dictionnaire des instances créées organisées par clé
        """
        if not model or not fields or not keys:
            return {}

        model_name = model._meta.model_name
        results = self.models.get(model_name, {})
        actions = actions or {}
        try:
            worksheet = self.workbook[sheet]
        except KeyError:
            return
        title = True
        for row_number, row in enumerate(worksheet.iter_rows()):
            # Ignore la ligne des en-têtes
            if title:
                title = False
                continue
            # Construit l'instance de l'entité
            obj = model()
            data = {}
            m2ms = {}
            for cell_number, cell in enumerate(row):
                # Récupère la valeur de la cellule
                if cell_number >= len(fields):
                    break
                field = fields[cell_number]
                value = cell.value
                if value is None or value == '':
                    continue
                try:
                    value = value.strip()
                except AttributeError:
                    pass
                # Analyse le type de champ (FK ou M2M)
                f = obj._meta.get_field(field)
                if f.remote_field and f.related_model and field not in actions:
                    related_class = f.related_model
                    rel_model_name = related_class._meta.model_name
                    models = results if rel_model_name == model_name else self.models[
                        f.related_model._meta.model_name]
                    if hasattr(f.remote_field, 'through'):
                        m2ms[field] = [models[code.strip()] for code in value.split(',')]
                        continue
                    else:
                        value = models[value]
                # Exécution d'une action spécifique sur le champ
                if field in actions:
                    action = actions[field]
                    value = action(value)
                data[field] = value
            if not data:
                break
            # Récupère l'entité depuis la base si elle existe
            filters = {key: data.get(key, None) for key in keys}
            if not force and model.objects.filter(**filters).exists():
                raise Exception("Un objet {} avec les clés {} existe déjà".format(model_name, filter))
            obj = model.objects.filter(**filters).first() or obj
            # Ajoute l'ordre dans le modèle
            if ordre and ordre not in data and hasattr(obj, ordre):
                data[ordre] = row_number
            # Sauvegarde l'entité
            for key, value in data.items():
                setattr(obj, key, value)
            try:
                obj.clean()
                obj.save(_ignore_log=not obj.pk, _force_default=True)
            except Exception as error:
                logger.error("[{}] {} ({})".format(model_name, obj, error))
                raise
            # Ajoute les many-to-many
            for field, values in m2ms.items():
                obj._ignore_log = True
                m2m = getattr(obj, field)
                m2m.clear()
                m2m.add(*values)
            # Conserve l'instance selon son code
            code = '|'.join(str(value.pk if hasattr(value, 'pk') else value)
                            for key, value in data.items() if key in keys)
            if code:
                results[code] = obj
            logger.info("[{}] {} (id: {})".format(model_name, obj, obj.pk))
        self.models[model_name] = results
        return results
