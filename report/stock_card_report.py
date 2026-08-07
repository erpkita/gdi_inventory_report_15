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

        product_ids = products.ids
        location_ids = self._get_location_ids(wizard.location_id.id)

        # All heavy lifting (opening balances + movement rows) is fetched in
        # ONE query each, for ALL products at once, instead of looping per
        # product with separate ORM searches + lazy-loaded related fields.
        # Quantities are converted to each product's own UoM in SQL (a move's
        # product_uom/product_uom_id is NOT guaranteed to match the product's
        # default UoM - e.g. received in "Box" but tracked in "Pcs" - so
        # summing the raw qty column would silently give the wrong balance).
        if wizard.get_from_move_line:
            openings = self._get_opening_balance_from_move_line(
                product_ids, location_ids, wizard.date_from
            )
            moves_by_product = self._get_moves_from_move_line(
                product_ids, location_ids, wizard.date_from, wizard.date_to
            )
        else:
            openings = self._get_opening_balance_from_move(
                product_ids, location_ids, wizard.date_from
            )
            moves_by_product = self._get_moves_from_move(
                product_ids, location_ids, wizard.date_from, wizard.date_to
            )

        location_ids_set = set(location_ids)
        result = []

        for product in products:
            opening = openings.get(product.id, 0.0)
            balance = opening
            lines = []

            for row in moves_by_product.get(product.id, []):
                qty_in = qty_out = 0.0

                if row['location_dest_id'] in location_ids_set:
                    qty_in = row['qty']
                    balance += qty_in
                if row['location_id'] in location_ids_set:
                    qty_out = row['qty']
                    balance -= qty_out

                lines.append({
                    'date': row['date'],
                    'product_name': product.display_name,
                    'reference': row['picking_name'] or row['move_reference'] or '',
                    'doc_type': self._get_move_type(row['picking_type_code']),
                    'source': row['source_name'] or '',
                    'destination': row['destination_name'] or '',
                    'lot': row.get('lot_name') or '',
                    'qty_in': qty_in,
                    'qty_out': qty_out,
                    'balance': balance,
                })

            result.append({
                'product': product,
                'product_name': product.display_name,
                'product_code': product.item_code_ref or '',
                'uom': product.uom_id.name,
                'opening_balance': opening,
                'closing_balance': balance,
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
    # LOCATION SCOPE (RAW SQL)
    # =====================================================
    def _get_location_ids(self, location_id):
        """Resolve the selected location + all its children in one query,
        using parent_path (the same mechanism the ORM uses for child_of)."""
        self.env.cr.execute("""
            SELECT child.id
            FROM stock_location child
            JOIN stock_location parent ON parent.id = %s
            WHERE child.parent_path LIKE parent.parent_path || '%%'
        """, (location_id,))
        return [row[0] for row in self.env.cr.fetchall()]

    # =====================================================
    # OPENING BALANCE (RAW SQL, ALL PRODUCTS IN ONE QUERY)
    # =====================================================
    # Both queries convert qty to the product's own UoM via `uom_uom.factor`
    # (amount = qty / source_uom.factor * product_uom.factor), the same
    # formula Odoo core uses in uom.uom._compute_quantity(). Without this,
    # a move recorded in a different UoM than the product's default silently
    # contributes the wrong number to a running total that spans the
    # product's ENTIRE history - the opening balance is the most exposed to
    # this since it's the aggregate most likely to include an old move that
    # used a different UoM.
    def _get_opening_balance_from_move(self, product_ids, location_ids, date_from):
        if not product_ids or not location_ids:
            return {}

        self.env.cr.execute("""
            SELECT
                sm.product_id,
                COALESCE(SUM(CASE WHEN sm.location_dest_id = ANY(%(loc_ids)s)
                    THEN sm.product_uom_qty / move_uom.factor * prod_uom.factor ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN sm.location_id = ANY(%(loc_ids)s)
                    THEN sm.product_uom_qty / move_uom.factor * prod_uom.factor ELSE 0 END), 0) AS balance
            FROM stock_move sm
            JOIN uom_uom move_uom ON move_uom.id = sm.product_uom
            JOIN product_product pp ON pp.id = sm.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN uom_uom prod_uom ON prod_uom.id = pt.uom_id
            WHERE sm.product_id = ANY(%(product_ids)s)
              AND sm.state = 'done'
              AND sm.date < %(date_from)s
              AND (sm.location_id = ANY(%(loc_ids)s) OR sm.location_dest_id = ANY(%(loc_ids)s))
            GROUP BY sm.product_id
        """, {
            'loc_ids': location_ids,
            'product_ids': product_ids,
            'date_from': date_from,
        })
        return {row[0]: row[1] for row in self.env.cr.fetchall()}

    def _get_opening_balance_from_move_line(self, product_ids, location_ids, date_from):
        if not product_ids or not location_ids:
            return {}

        self.env.cr.execute("""
            SELECT
                sml.product_id,
                COALESCE(SUM(CASE WHEN sml.location_dest_id = ANY(%(loc_ids)s)
                    THEN sml.qty_done / move_uom.factor * prod_uom.factor ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN sml.location_id = ANY(%(loc_ids)s)
                    THEN sml.qty_done / move_uom.factor * prod_uom.factor ELSE 0 END), 0) AS balance
            FROM stock_move_line sml
            JOIN uom_uom move_uom ON move_uom.id = sml.product_uom_id
            JOIN product_product pp ON pp.id = sml.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN uom_uom prod_uom ON prod_uom.id = pt.uom_id
            WHERE sml.product_id = ANY(%(product_ids)s)
              AND sml.state = 'done'
              AND sml.date < %(date_from)s
              AND (sml.location_id = ANY(%(loc_ids)s) OR sml.location_dest_id = ANY(%(loc_ids)s))
            GROUP BY sml.product_id
        """, {
            'loc_ids': location_ids,
            'product_ids': product_ids,
            'date_from': date_from,
        })
        return {row[0]: row[1] for row in self.env.cr.fetchall()}

    # =====================================================
    # MOVEMENT ROWS (RAW SQL, ALL PRODUCTS IN ONE QUERY)
    # =====================================================
    def _get_moves_from_move(self, product_ids, location_ids, date_from, date_to):
        if not product_ids or not location_ids:
            return {}

        self.env.cr.execute("""
            SELECT
                sm.product_id AS product_id,
                sm.date AS date,
                sm.product_uom_qty / move_uom.factor * prod_uom.factor AS qty,
                sm.location_id AS location_id,
                sm.location_dest_id AS location_dest_id,
                src.complete_name AS source_name,
                dest.complete_name AS destination_name,
                sp.name AS picking_name,
                sm.reference AS move_reference,
                spt.code AS picking_type_code,
                NULL AS lot_name
            FROM stock_move sm
            JOIN uom_uom move_uom ON move_uom.id = sm.product_uom
            JOIN product_product pp ON pp.id = sm.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN uom_uom prod_uom ON prod_uom.id = pt.uom_id
            LEFT JOIN stock_picking sp ON sp.id = sm.picking_id
            LEFT JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
            LEFT JOIN stock_location src ON src.id = sm.location_id
            LEFT JOIN stock_location dest ON dest.id = sm.location_dest_id
            WHERE sm.product_id = ANY(%(product_ids)s)
              AND sm.state = 'done'
              AND sm.date >= %(date_from)s
              AND sm.date <= %(date_to)s
              AND (sm.location_id = ANY(%(loc_ids)s) OR sm.location_dest_id = ANY(%(loc_ids)s))
            ORDER BY sm.product_id, sm.date ASC, sm.id ASC
        """, {
            'product_ids': product_ids,
            'loc_ids': location_ids,
            'date_from': date_from,
            'date_to': date_to,
        })
        return self._group_rows_by_product(self.env.cr.dictfetchall())

    def _get_moves_from_move_line(self, product_ids, location_ids, date_from, date_to):
        if not product_ids or not location_ids:
            return {}

        self.env.cr.execute("""
            SELECT
                sml.product_id AS product_id,
                sml.date AS date,
                sml.qty_done / move_uom.factor * prod_uom.factor AS qty,
                sml.location_id AS location_id,
                sml.location_dest_id AS location_dest_id,
                src.complete_name AS source_name,
                dest.complete_name AS destination_name,
                sp.name AS picking_name,
                sm.reference AS move_reference,
                spt.code AS picking_type_code,
                lot.name AS lot_name
            FROM stock_move_line sml
            JOIN stock_move sm ON sm.id = sml.move_id
            JOIN uom_uom move_uom ON move_uom.id = sml.product_uom_id
            JOIN product_product pp ON pp.id = sml.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN uom_uom prod_uom ON prod_uom.id = pt.uom_id
            LEFT JOIN stock_picking sp ON sp.id = sml.picking_id
            LEFT JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
            LEFT JOIN stock_location src ON src.id = sml.location_id
            LEFT JOIN stock_location dest ON dest.id = sml.location_dest_id
            LEFT JOIN stock_production_lot lot ON lot.id = sml.lot_id
            WHERE sml.product_id = ANY(%(product_ids)s)
              AND sml.state = 'done'
              AND sml.date >= %(date_from)s
              AND sml.date <= %(date_to)s
              AND (sml.location_id = ANY(%(loc_ids)s) OR sml.location_dest_id = ANY(%(loc_ids)s))
            ORDER BY sml.product_id, sml.date ASC, sml.id ASC
        """, {
            'product_ids': product_ids,
            'loc_ids': location_ids,
            'date_from': date_from,
            'date_to': date_to,
        })
        return self._group_rows_by_product(self.env.cr.dictfetchall())

    @staticmethod
    def _group_rows_by_product(rows):
        grouped = {}
        for row in rows:
            grouped.setdefault(row['product_id'], []).append(row)
        return grouped

    # =====================================================
    # DOC TYPE
    # =====================================================
    def _get_move_type(self, picking_type_code):
        return {
            'incoming': 'Receipt',
            'outgoing': 'Delivery',
            'internal': 'Internal Transfer',
        }.get(picking_type_code, 'Movement')
