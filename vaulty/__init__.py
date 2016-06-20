#!/usr/bin/env python

import os
import click
import shelve
from pprint import pprint
from datetime import datetime

import vault
import vimeo_download


class Vaulty(object):
    base_path = None
    boto_client = None

    def __init__(self, base_path, boto_client):
        if base_path and not os.path.exists(base_path):
            base_path = None
        else:
            base_path = os.path.abspath(base_path)

        self.base_path = base_path if base_path else os.path.abspath(
            os.path.curdir)
        self.boto_client = boto_client


@click.group()
@click.option('-b', '--base_path', default='', help='base path (default: current directory)')  # nopep8
@click.pass_context
def cli(ctx, base_path):
    ctx.obj = Vaulty(base_path, vault.BotoClient())


@cli.command()
@click.option('-p', '--platform', type=click.Choice(['openhpi', 'opensap', 'moochouse']), help='Platform')  # nopep8
@click.option('-v', '--vault_name', default='', help='vault name')
@click.pass_context
def upload(ctx, platform, vault_name):
    """
    download videos from vimeo and upload them to a glacier vault

    :param ctx: context object
    :param platform: platform name
    :param vault_name: vault name
    :return: None
    """
    vault_name = vault_name if vault_name else 'videos_{0}'.format(platform)

    glacier_vault = vault.GlacierVault(ctx.obj.boto_client)
    vaults_list = glacier_vault.list_vaults()

    if not filter(lambda x: x['VaultName'] == vault_name, vaults_list):
        pprint('Vault does not exist, create vault:')
        pprint(glacier_vault.create_vault(vault_name))

    timestr = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

    logdb_vault = shelve.open(
        os.path.join(ctx.obj.base_path, 'vault_{0}_{1}.db'.format(
            platform, timestr)))

    logdb_vimeo = shelve.open(
        os.path.join(ctx.obj.base_path, 'vimeo_{0}_{1}.db'.format(
            platform, timestr)))

    temp_file = os.path.join(ctx.obj.base_path, 'tmpfile_{0}.mp4'.format(
        platform, timestr))

    glacier_upload = vault.GlacierUpload(
        ctx.obj.boto_client, vault_name, logdb_vault)

    vimeo_downloader = vimeo_download.VimeoDownloader(
       platform, glacier_upload.upload, logdb_vimeo, temp_file)
    vimeo_downloader.iterate_pages(per_page=25)

    logdb_vimeo.close()
    logdb_vault.close()


@cli.command()
@click.pass_context
def list_vaults(ctx):
    """
    list all glacier vaults

    :param ctx: context object
    :return:
    """
    pprint(vault.GlacierVault(ctx.obj.boto_client).list_vaults())


@cli.command()
@click.option('-v', '--vault_name', help='vault name')
@click.pass_context
def get_vault_jobs(ctx, vault_name):
    """
    list all open glacier vault's jobs

    :param ctx: context object
    :param vault_name: glacier vault name
    :return: None
    """
    pprint(vault.GlacierVault(ctx.obj.boto_client).get_vault_jobs(vault_name))


@cli.command()
@click.option('-v', '--vault_name', help='vault name')
@click.option('-j', '--job_id', help='job id')
@click.pass_context
def get_job_output(ctx, vault_name, job_id):
    """
    get a glacier vault job's output

    :param ctx: context object
    :param vault_name: glacier vault name
    :param job_id: glacier vault job id
    :return: None
    """
    pprint(vault.GlacierVault(ctx.obj.boto_client).get_job_output(
        vault_name, job_id))


@cli.command()
@click.option('-v', '--vault_name', help='vault name')
@click.pass_context
def delete_archives(ctx, vault_name):
    """
    delete archives from glacier vault

    :param ctx: context object
    :param vault_name: glacier vault name
    :return: None

    - create SNS topic
    - set vault notification
    - create SQS queue
    - add permission to write to queue
    - subscribe to SNS
    - init_inventory_retrieval
    - wait for SNS/SQS nofification
    - delete Glacier archives
    """
    sns = vault.SNS(ctx.obj.boto_client)
    sqs = vault.SQS(ctx.obj.boto_client)
    gv = vault.GlacierVault(ctx.obj.boto_client)

    sns_topic_arn = sns.create_sns_topic(vault_name)
    gv.set_sns_vault_notifications(vault_name, sns_topic_arn)

    sqs_queue_url, sqs_queue_arn = sqs.create_queue(vault_name, delay=0)
    sqs.set_policy(sqs_queue_url, sqs_queue_arn)
    sns_subscription_arn = sns.subscribe(sns_topic_arn, sqs_queue_arn)

    pprint({
        "SNS_TopicArn": sns_topic_arn,
        "SNS_SubscriptionArn": sns_subscription_arn,
        "SQS_QueueuUrl": sqs_queue_url,
        "SQS_QueueuArn": sqs_queue_arn,
    })

    pprint(gv.init_inventory_retrieval(vault_name))

    def handle_sns_notification(sns_notification):
        job_id = sns_notification['JobId']
        job_output = gv.get_job_output(vault_name, job_id)

        for archive in job_output['ArchiveList']:
            pprint("DELETE {0:s} from {1:s}".format(archive['ArchiveId'],
                                                    vault_name))
            pprint(gv.delete_archive(
                vault_name=vault_name,
                archive_id=archive['ArchiveId']
            ))

    sqs.receive_message(sqs_queue_url, handle_sns_notification)


@cli.command()
@click.option('-v', '--vault_name', help='vault name')  # nopep8
@click.pass_context
def get_vault_inventory(ctx, vault_name):
    """
    list glacier vault's archives

    :param ctx: context object
    :param vault_name: vault name
    :return: None
    """
    sns = vault.SNS(ctx.obj.boto_client)
    sqs = vault.SQS(ctx.obj.boto_client)
    gv = vault.GlacierVault(ctx.obj.boto_client)

    sns_topic_arn = sns.create_sns_topic(vault_name)
    gv.set_sns_vault_notifications(vault_name, sns_topic_arn)

    sqs_queue_url, sqs_queue_arn = sqs.create_queue(vault_name, delay=0)
    sqs.set_policy(sqs_queue_url, sqs_queue_arn)
    sns_subscription_arn = sns.subscribe(sns_topic_arn, sqs_queue_arn)

    pprint({
        "SNS_TopicArn": sns_topic_arn,
        "SNS_SubscriptionArn": sns_subscription_arn,
        "SQS_QueueuUrl": sqs_queue_url,
        "SQS_QueueuArn": sqs_queue_arn,
    })

    pprint(gv.init_inventory_retrieval(vault_name))

    def handle_sns_notification(sns_notification):
        job_id = sns_notification['JobId']
        job_output = gv.get_job_output(vault_name, job_id)

        db = shelve.open('{0}_inventory.db'.format(vault_name))

        for archive in job_output['ArchiveList']:
            db[archive['ArchiveId']] = archive

        db.close()

    sqs.receive_message(sqs_queue_url, handle_sns_notification)


if __name__ == '__main__':
    cli()