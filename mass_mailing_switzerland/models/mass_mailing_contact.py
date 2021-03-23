##############################################################################
#
#    Copyright (C) 2020 Compassion CH (http://www.compassion.ch)
#    Releasing children from poverty in Jesus' name
#    @author: Emanuel Cino <ecino@compassion.ch>
#
#    The licence is in the file __manifest__.py
#
##############################################################################
from ast import literal_eval
from time import sleep

from odoo import api, models, fields
from odoo.addons.queue_job.job import job


class MassMailingContact(models.Model):
    _inherit = "mail.mass_mailing.contact"

    partner_id = fields.Many2one(
        "res.partner", "Associated contact", ondelete="cascade",
    )

    @api.model
    def create(self, vals_list):
        records = super().create(vals_list)
        for contact in records:
            if not contact.partner_id:
                contact.write({"partner_id": contact.get_partner(contact.email)})
        return records

    @api.multi
    def get_partner(self, email):
        """Override to fetch partner directly from relation if set."""
        self.ensure_one()
        if self.partner_id:
            return self.partner_id.id
        else:
            return self.env["res.partner"].search([
                ("email", "=ilike", email),
                ("contact_type", "=", "standalone"),
                ("opt_out", "!=", True)
            ], limit=1).id

    @api.multi
    def write(self, values):
        out = super().write(values)
        # can't be simplified because we are looking for is_email_valid == False
        # AND is_email_valid is in values (return None otherwise)
        if values.get('is_email_valid') is False:
            for mailing_contact in self:
                # assure is_valid_email as not been reset (compute field might have been trigger)
                if mailing_contact.is_email_valid:
                    mailing_contact.is_email_valid = False

        return out

    @job(default_channel="root.mass_mailing_switzerland.update_partner_mailchimp")
    @api.model
    def update_partner_merge_fields_job(self, partner_id):
        """Update one contact merge fields.
        Preferred approach to avoid overloading the network.
        :return: True or False
        """
        mailing_contact = self.search([("partner_id", "=", partner_id)], limit=1)
        if mailing_contact:
            try:
                mailing_contact.action_update_to_mailchimp()
            except:
                return False
        return True

    @job(default_channel="root.mass_mailing_switzerland.update_mailchimp")
    @api.model
    def update_all_merge_fields_job(self, partner_ids=None):
        """ Update all contacts merge fields
        :return: List of partner_ids that failed to update
        """
        search_criterias = [("partner_id", "!=", False)]
        if partner_ids:
            search_criterias.append(("partner_id", "in", partner_ids))
        failed = []
        for mailing_contact in self.search(search_criterias):
            try:
                mailing_contact.action_update_to_mailchimp()
            except:
                self.env.clear()
                failed.append(mailing_contact.partner_id.id)
                # Allows network to recover
                sleep(1)
        return failed

    @api.multi
    def action_export_to_mailchimp(self):
        """
        Always export in english so that all tags are set in English
        """
        if self.env.context.get("skip_mailchimp"):
            return True
        return super(MassMailingContact,
                     self.with_context(lang="en_US")).action_export_to_mailchimp()

    @api.multi
    def action_update_to_mailchimp(self):
        """
        Always export in english so that all tags are set in English
        """
        if self.env.context.get("skip_mailchimp"):
            return True

        for contact_to_update in self:
            # if previous write failed reference to mailchimp member will be lost. error 404
            try:
                return super(MassMailingContact,
                             contact_to_update.with_context(lang="en_US")).action_update_to_mailchimp()
            except Exception as e:

                available_mailchimp_lists = self.env['mailchimp.lists'].search([])
                lists = available_mailchimp_lists.mapped('odoo_list_id').ids

                # if not contact were fond. means an error occur at last write().
                # Email field in odoo and mailchimp are now different.
                # solution : we remove previous link to mailchimp and export the contact with new mail
                if e.args[0] and literal_eval(e.args[0])['status'] == 404:
                    contact_to_update.subscription_list_ids.filtered(
                        lambda x: x.list_id.id in lists).write({"mailchimp_id": False})
                # raise exception if it's any other type
                else:
                    raise e

                # once link is remove member can again be exported to mailchimp
                return super(MassMailingContact,
                             self.with_context(lang="en_US")).action_export_to_mailchimp()

    @api.multi
    def action_archive_from_mailchimp(self):
        available_mailchimp_lists = self.env['mailchimp.lists'].search([])
        lists = available_mailchimp_lists.mapped('odoo_list_id').ids
        for record in self:
            lists_to_export = record.subscription_list_ids.filtered(
                lambda x: x.list_id.id in lists and x.mailchimp_id)
            for list in lists_to_export:
                mailchimp_list_id = list.list_id.mailchimp_list_id
                mailchimp_list_id.account_id._send_request(
                    'lists/%s/members/%s' % (mailchimp_list_id.list_id, list.md5_email),
                    {}, method='DELETE')
        return True

    @api.multi
    def unlink(self):
        self.action_archive_from_mailchimp()
        return super().unlink()
