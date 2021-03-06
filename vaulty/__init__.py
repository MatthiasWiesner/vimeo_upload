#!/usr/bin/env python

import os
import re
import click
import shelve
import random
import hashlib
from pprint import pprint
from datetime import datetime

import vault
import vimeo_download

inventories_bucket_name = 'vaultinventories'


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
@click.option('-b', '--bucket_name', default='', help='bucket name')
@click.pass_context
def test(ctx, bucket_name):
    platform = 'openwho'
    bucket = 'openwho-videos'

    s3 = vault.S3(ctx.obj.boto_client)
    s3.create_private_bucket(bucket)

    timestr = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

    logdb_s3 = shelve.open(
        os.path.join(ctx.obj.base_path, 's3_{0}_{1}.db'.format(
            bucket, timestr)))

    logdb_vimeo = shelve.open(
        os.path.join(ctx.obj.base_path, 'vimeo_{0}_{1}.db'.format(
            platform, timestr)))

    s3upload = vault.S3Upload(ctx.obj.boto_client, bucket, logdb_s3)

    vimeo_downloader = vimeo_download.VimeoDownloader(
       platform, s3upload.upload, logdb_vimeo)
    vimeo_downloader.iterate_pages(per_page=25)

    logdb_vimeo.close()
    logdb_s3.close()


@cli.command()
@click.option('-b', '--bucket_name', default='', help='bucket name')
@click.pass_context
def backup_s3_bucket(ctx, bucket_name):
    """
    download all bucket objects and upload them to a glacier vault

    :param ctx: context object
    :param bucket_name: buckets name
    :return: None
    """
    s3 = vault.S3(ctx.obj.boto_client)
    bucket_list = s3.get_bucket_name_list()
    if bucket_name not in bucket_list:
        raise Exception('Bucket could not be found')

    if inventories_bucket_name not in bucket_list:
        s3.create_private_bucket(inventories_bucket_name)

    vault_name = _clean_str('{0}_s3bucket_backup_{1}'.format(
        bucket_name, random.randint(10000, 99999)))

    logdb_vault_name = '{0}_backup.db'.format(vault_name)
    logdb_vault_path = '{0}/{1}'.format(os.getcwd(), logdb_vault_name)
    logdb_vault = shelve.open(logdb_vault_path)

    glacier_vault = vault.GlacierVault(ctx.obj.boto_client)
    vaults_list = glacier_vault.list_vaults()

    if not filter(lambda x: x['VaultName'] == vault_name, vaults_list):
        pprint('Vault does not exist, create vault:')
        pprint(glacier_vault.create_vault(vault_name))
    else:
        raise Exception("Vault does already exist. Delete the vault and its inventory to proceed.")  # nopep8

    glacier_upload = vault.GlacierUpload(
        ctx.obj.boto_client, vault_name, logdb_vault)

    for inventory_obj in s3.get_bucket_contents(bucket_name):
        key = inventory_obj['Key'].encode('utf-8')
        bucket_obj = s3.get_object(bucket_name, key)
        keyencoded = hashlib.md5(key).hexdigest()

        logdb_vault[keyencoded] = dict()
        logdb_vault[keyencoded]['Key'] = key
        glacier_upload.upload(keyencoded, bucket_obj['Body'].read())

    logdb_vault.close()
    s3.put_object_from_file(inventories_bucket_name, logdb_vault_name, logdb_vault_path)


@cli.command()
@click.option('-p', '--platform', type=click.Choice(['openhpi', 'opensap', 'moochouse', 'openwho']), help='Platform')  # nopep8
@click.option('-v', '--vault_name', default='', help='vault name')
@click.pass_context
def upload_vimeo_videos(ctx, platform, vault_name):
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
            vault_name, timestr)))

    logdb_vimeo = shelve.open(
        os.path.join(ctx.obj.base_path, 'vimeo_{0}_{1}.db'.format(
            platform, timestr)))

    glacier_upload = vault.GlacierUpload(
        ctx.obj.boto_client, vault_name, logdb_vault)

    vimeo_downloader = vimeo_download.VimeoDownloader(
       platform, glacier_upload.upload, logdb_vimeo)
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


def _delete_archives_from_logfile(boto_client, vault_name, logfile):
    s3 = vault.S3(boto_client)
    gv = vault.GlacierVault(boto_client)

    # create a shelve from the in S3 stored logfile
    response = s3.get_object(inventories_bucket_name, logfile)
    with open(logfile, 'r') as f:
        f.write(response['Body'].read())

    logdb = shelve.open(logfile)
    for value in logdb.values():
        archive_id = value['response']['archiveId']
        try:
            pprint(gv.delete_archive(
                vault_name=vault_name,
                archive_id=archive_id
            ))
        except Exception as e:
            print 'An error occured with {0}: {1}'.format(archive_id, str(e))


@cli.command()
@click.option('-v', '--vault_name', help='vault name')
@click.option('-l', '--logfile', required=False, help='logfile on S3')
@click.pass_context
def delete_archives(ctx, vault_name, logfile=None):
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

    - delete Glacier vault archives
    """
    if logfile:
        _delete_archives_from_logfile(ctx.obj.boto_client, vault_name, logfile)
        return

    sns = vault.SNS(ctx.obj.boto_client)
    sqs = vault.SQS(ctx.obj.boto_client)
    gv = vault.GlacierVault(ctx.obj.boto_client)

    sns_topic_arn = sns.create_sns_topic(_clean_str(vault_name))
    gv.set_sns_vault_notifications(vault_name, sns_topic_arn)

    sqs_queue_url, sqs_queue_arn = sqs.create_queue(_clean_str(vault_name), delay=0)
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
            archive_id = str(archive['ArchiveId']).encode('utf-8')
            pprint("DELETE {0:s} from {1:s}".format(archive_id,
                                                    vault_name))
            pprint(gv.delete_archive(
                vault_name=vault_name,
                archive_id=archive_id
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

    sns_topic_arn = sns.create_sns_topic(_clean_str(vault_name))
    gv.set_sns_vault_notifications(vault_name, sns_topic_arn)

    sqs_queue_url, sqs_queue_arn = sqs.create_queue(_clean_str(vault_name), delay=0)
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
            db[str(archive['ArchiveId']).encode('utf-8')] = archive

        db.close()

    sqs.receive_message(sqs_queue_url, handle_sns_notification)


def _clean_str(s):
    return re.sub('[^a-zA-Z0-9_-]', '', s)


if __name__ == '__main__':
    cli()
