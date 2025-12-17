# -*- coding: utf-8 -*-

from odoo import api, models, fields, _
from odoo.exceptions import UserError


class StockCardReport(models.AbstractModel):
    _name = 'report.gdi_inventory_report_15.report_stock_card_document'
    _description = 'Stock Card Report'

    # =====================================================
    # MAIN
    # =====================================================
    @api.model
    def _get_report_values(self, docids, data=None):
        if not data:
            raise UserError(_("Report data is missing."))

        wizard = self.env['stock.card.wizard'].browse(data.get('wizard_id'))
        if not wizard.exists():
            raise UserError(_("Wizard data not found."))

        report_data = self._prepare_report_data(wizard)

        return {
            'doc_ids': docids,
            'doc_model': 'stock.card.wizard',
            'docs': wizard,
            'data': {
                'date_from': wizard.date_from,
                'date_to': wizard.date_to,
                'date_from_formatted': wizard.date_from.strftime('%d/%m/%Y'),
                'date_to_formatted': wizard.date_to.strftime('%d/%m/%Y'),
                'warehouse': wizard.warehouse_id.name,
                'location': wizard.location_id.complete_name,
                'brand': wizard.brand_id.name if wizard.brand_id else 'All Brands',
                'use_move_line': wizard.get_from_move_line,
                'report_data': report_data,
            },
            'company': self.env.company,
        }

    # =====================================================
    # CORE LOGIC
    # =====================================================
    def _prepare_report_data(self, wizard):
        products = self._get_products(wizard)
        if not products:
            raise UserError(_('No products found.'))

        locations = self.env['stock.location'].search([
            ('id', 'child_of', wizard.location_id.id)
        ])
        location_ids = locations.ids

        result = []

        for product in products:
            if wizard.get_from_move_line:
                opening = self._opening_from_move_line(product, location_ids, wizard.date_from)
                lines, closing = self._moves_from_move_line(
                    product, location_ids, wizard.date_from, wizard.date_to, opening
                )
            else:
                opening = self._opening_from_move(product, location_ids, wizard.date_from)
                lines, closing = self._moves_from_move(
                    product, location_ids, wizard.date_from, wizard.date_to, opening
                )

            result.append({
                'product': product,
                'product_name': product.display_name,
                'product_code': product.item_code_ref or '',
                'uom': product.uom_id.name,
                'opening_balance': opening,
                'closing_balance': closing,
                'moves': lines,
            })

        return result

    # =====================================================
    # PRODUCT FILTER
    # =====================================================
    def _get_products(self, wizard):
        if wizard.product_ids:
            return wizard.product_ids
        if wizard.brand_id:
            return self.env['product.product'].search([
                ('product_tmpl_id.brand_id', '=', wizard.brand_id.id)
            ])
        return self.env['product.product'].search([])

    # =====================================================
    # STOCK.MOVE VERSION
    # =====================================================
    def _opening_from_move(self, product, location_ids, date_from):
        moves = self.env['stock.move'].search([
            ('product_id', '=', product.id),
            ('state', '=', 'done'),
            ('date', '<', date_from),
            '|',
            ('location_id', 'in', location_ids),
            ('location_dest_id', 'in', location_ids)
        ])

        balance = 0.0
        for m in moves:
            if m.location_dest_id.id in location_ids:
                balance += m.product_uom_qty
            if m.location_id.id in location_ids:
                balance -= m.product_uom_qty
        return balance

    def _moves_from_move(self, product, location_ids, date_from, date_to, opening):
        moves = self.env['stock.move'].search([
            ('product_id', '=', product.id),
            ('state', '=', 'done'),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            '|',
            ('location_id', 'in', location_ids),
            ('location_dest_id', 'in', location_ids)
        ], order='date asc, id asc')

        balance = opening
        lines = []

        for m in moves:
            qty_in = qty_out = 0.0

            if m.location_dest_id.id in location_ids:
                qty_in = m.product_uom_qty
                balance += qty_in
            if m.location_id.id in location_ids:
                qty_out = m.product_uom_qty
                balance -= qty_out

            lines.append({
                'date': m.date,
                'product_name': product.display_name,
                'reference': m.picking_id.name if m.picking_id else (m.reference or ''),
                'doc_type': self._get_move_type(m),
                'source': m.location_id.complete_name,
                'destination': m.location_dest_id.complete_name,
                'lot': '',
                'qty_in': qty_in,
                'qty_out': qty_out,
                'balance': balance,
            })

        return lines, balance

    # =====================================================
    # STOCK.MOVE.LINE VERSION (EXPERIMENTAL)
    # =====================================================
    def _opening_from_move_line(self, product, location_ids, date_from):
        lines = self.env['stock.move.line'].search([
            ('product_id', '=', product.id),
            ('state', '=', 'done'),
            ('date', '<', date_from),
            '|',
            ('location_id', 'in', location_ids),
            ('location_dest_id', 'in', location_ids)
        ])

        balance = 0.0
        for l in lines:
            if l.location_dest_id.id in location_ids:
                balance += l.qty_done
            if l.location_id.id in location_ids:
                balance -= l.qty_done
        return balance

    def _moves_from_move_line(self, product, location_ids, date_from, date_to, opening):
        lines_rec = self.env['stock.move.line'].search([
            ('product_id', '=', product.id),
            ('state', '=', 'done'),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            '|',
            ('location_id', 'in', location_ids),
            ('location_dest_id', 'in', location_ids)
        ], order='date asc, id asc')

        balance = opening
        lines = []

        for l in lines_rec:
            qty_in = qty_out = 0.0

            if l.location_dest_id.id in location_ids:
                qty_in = l.qty_done
                balance += qty_in
            if l.location_id.id in location_ids:
                qty_out = l.qty_done
                balance -= qty_out

            lines.append({
                'date': l.date,
                'product_name': product.display_name,
                'reference': (
                    l.move_id.picking_id.name
                    if l.move_id.picking_id
                    else l.move_id.reference or ''
                ),
                'doc_type': self._get_move_type(l.move_id),
                'source': l.location_id.complete_name,
                'destination': l.location_dest_id.complete_name,
                'lot': l.lot_id.name if l.lot_id else '',
                'qty_in': qty_in,
                'qty_out': qty_out,
                'balance': balance,
            })

        return lines, balance

    # =====================================================
    # DOC TYPE
    # =====================================================
    def _get_move_type(self, move):
        if move.picking_id:
            code = move.picking_id.picking_type_id.code
            return {
                'incoming': 'Receipt',
                'outgoing': 'Delivery',
                'internal': 'Internal Transfer',
            }.get(code, 'Movement')

        if move.inventory_id:
            return 'Adjustment'

        return 'Movement'
